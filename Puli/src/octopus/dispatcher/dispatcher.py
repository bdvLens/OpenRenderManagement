"""
.. module:: Dispatcher
   :platform: Unix
   :synopsis: A useful module indeed.
"""

from __future__ import with_statement

import logging
import socket
import time
from Queue import Queue
from itertools import groupby
import collections
try:
    import simplejson as json
except ImportError:
    import json

# from octopus.core import tools
from octopus.core import singletonconfig, singletonstats

from octopus.core.threadpool import ThreadPool, makeRequests, NoResultsPending
from octopus.core.framework import MainLoopApplication

from octopus.dispatcher.model import (DispatchTree, FolderNode, RenderNode,
                                      Pool, PoolShare, enums)
from octopus.dispatcher.strategies import FifoStrategy

from octopus.dispatcher import settings
from octopus.dispatcher.db.pulidb import PuliDB
from octopus.dispatcher.model.enums import *
from octopus.dispatcher.poolman.filepoolman import FilePoolManager
from octopus.dispatcher.poolman.wspoolman import WebServicePoolManager
from octopus.dispatcher.licenses.licensemanager import LicenseManager


LOGGER = logging.getLogger('dispatcher')


class Dispatcher(MainLoopApplication):
    '''The Dispatcher class is the core of the dispatcher application.
    It computes the assignments of commands to workers according to a
    DispatchTree and handles all the communications with the workers and
    clients.
    '''

    instance = None
    init = False

    def __new__(cls, framework):
        if cls.instance is None:
            cls.instance = super(Dispatcher, cls).__new__(cls, framework)
        return cls.instance

    def __init__(self, framework):
        if self.init:
            return
        self.init = True
        self.nextCycle = time.time()

        MainLoopApplication.__init__(self, framework)

        self.threadPool = ThreadPool(16, 0, 0, None)

        #
        # Class holding custom infos on the dispatcher.
        # This data can be periodically flushed in a specific log file for later use
        #
        

        self.cycle = 1
        self.dispatchTree = DispatchTree()
        self.licenseManager = LicenseManager()
        self.enablePuliDB = settings.DB_ENABLE
        self.cleanDB = settings.DB_CLEAN_DATA

        self.pulidb = None
        if self.enablePuliDB:
            self.pulidb = PuliDB(self.cleanDB, self.licenseManager)

        self.dispatchTree.registerModelListeners()
        rnsAlreadyInitialized = self.initPoolsDataFromBackend()

        if self.enablePuliDB and not self.cleanDB:
            LOGGER.warning("reloading jobs from database")
            beginTime = time.time()
            self.pulidb.restoreStateFromDb(self.dispatchTree, rnsAlreadyInitialized)
            LOGGER.warning("reloading took %.2fs" % (time.time() - beginTime))
            LOGGER.warning("done reloading jobs from database")
            LOGGER.warning("reloaded %d tasks" % len(self.dispatchTree.tasks))
        LOGGER.warning("checking dispatcher state")

        self.dispatchTree.updateCompletionAndStatus()
        self.updateRenderNodes()
        self.dispatchTree.validateDependencies()
        if self.enablePuliDB and not self.cleanDB:
            self.dispatchTree.toModifyElements = []
        self.defaultPool = self.dispatchTree.pools['default']
        LOGGER.warning("loading dispatch rules")
        self.loadRules()
        # it should be better to have a maxsize
        self.queue = Queue(maxsize=10000)

    def initPoolsDataFromBackend(self):
        '''
        Loads pools and workers from appropriate backend.
        '''
        try:
            if settings.POOLS_BACKEND_TYPE == "file":
                manager = FilePoolManager()
            elif settings.POOLS_BACKEND_TYPE == "ws":
                manager = WebServicePoolManager()
            elif settings.POOLS_BACKEND_TYPE == "db":
                return False
        except Exception:
            return False

        computers = manager.listComputers()

        ### recreate the pools
        poolsList = manager.listPools()
        poolsById = {}
        for poolDesc in poolsList:
            pool = Pool(id=int(poolDesc.id), name=str(poolDesc.name))
            self.dispatchTree.toCreateElements.append(pool)
            poolsById[pool.id] = pool

        ### recreate the rendernodes
        rnById = {}
        for computerDesc in computers:
            try:
                computerDesc.name = socket.getfqdn(computerDesc.name)
                ip = socket.gethostbyname(computerDesc.name)
            except socket.gaierror:
                continue
            renderNode = RenderNode(computerDesc.id, computerDesc.name + ":" + str(computerDesc.port), computerDesc.cpucount * computerDesc.cpucores, computerDesc.cpufreq, ip, computerDesc.port, computerDesc.ramsize, json.loads(computerDesc.properties))
            self.dispatchTree.toCreateElements.append(renderNode)
            ## add the rendernodes to the pools
            for pool in computerDesc.pools:
                poolsById[pool.id].renderNodes.append(renderNode)
                renderNode.pools.append(poolsById[pool.id])
            self.dispatchTree.renderNodes[str(renderNode.name)] = renderNode
            rnById[renderNode.id] = renderNode

        # add the pools to the dispatch tree
        for pool in poolsById.values():
            self.dispatchTree.pools[pool.name] = pool
        if self.cleanDB or not self.enablePuliDB:
            graphs = FolderNode(1, "graphs", self.dispatchTree.root, "root", 0, 0, 0, FifoStrategy())
            self.dispatchTree.toCreateElements.append(graphs)
            self.dispatchTree.nodes[graphs.id] = graphs
            ps = PoolShare(1, self.dispatchTree.pools["default"], graphs, PoolShare.UNBOUND)
            self.dispatchTree.toCreateElements.append(ps)
        if self.enablePuliDB:
            # clean the tables pools and rendernodes (overwrite)
            self.pulidb.dropPoolsAndRnsTables()
            self.pulidb.createElements(self.dispatchTree.toCreateElements)
            self.dispatchTree.resetDbElements()

        return True

    def loadRules(self):
        from .rules.graphview import GraphViewBuilder
        graphs = self.dispatchTree.findNodeByPath("/graphs", None)
        if graphs is None:
            LOGGER.fatal("No /graphs node, impossible to load rule for /graphs.")
            self.stop()
        self.dispatchTree.rules.append(GraphViewBuilder(self.dispatchTree, graphs))


    def prepare(self):
        pass

    def stop(self):
        '''Stops the application part of the dispatcher.'''
        #self.httpRequester.stopAll()
        pass

    @property
    def modified(self):
        return bool(self.dispatchTree.toArchiveElements or
                    self.dispatchTree.toCreateElements or
                    self.dispatchTree.toModifyElements)

    def mainLoop(self):
        '''
        | Dispatcher main loop iteration.
        | Periodically called with tornado'sinternal callback mecanism, the frequency is defined by config: CORE.MASTER_UPDATE_INTERVAL
        | During this process, the dispatcher will:
        |   - update completion and status for all jobs in dispatchTree
        |   - update status of renderNodes
        |   - validate inter tasks dependencies
        |   - update the DB with recorded changes in the model
        |   - compute new assignments and send them to the proper rendernodes
        |   - release all finished jobs/rns
        '''
        
        # JSA DEBUG: timer pour profiler les etapes       

        loopStartTime = time.time()
        prevTimer = loopStartTime

        if singletonconfig.get('CORE','GET_STATS'):
            singletonstats.theStats.cycleDate = loopStartTime

        LOGGER.info("")
        LOGGER.info("-----------------------------------------------------")
        LOGGER.info(" Start dispatcher process cycle.")
        LOGGER.info("-----------------------------------------------------")

        # JSA: Check if requests are finished (necessaire ?)
        try:
            self.threadPool.poll()
        except NoResultsPending:
            pass
        else:
            LOGGER.info("finished some network requests")

        self.cycle += 1

        # Update of allocation is done when parsing the tree for completion and status update (done partially for invalidated node only i.e. when needed)
        self.dispatchTree.updateCompletionAndStatus()
        if singletonconfig.get('CORE','GET_STATS'):
            singletonstats.theStats.cycleTimers['update_tree'] = time.time() - prevTimer
        LOGGER.info("%8.2f ms --> update completion status" % ( (time.time() - prevTimer)*1000 ) )
        prevTimer = time.time()

        # Update render nodes
        self.updateRenderNodes()
        if singletonconfig.get('CORE','GET_STATS'):
            singletonstats.theStats.cycleTimers['update_rn'] = time.time() - prevTimer
        LOGGER.info("%8.2f ms --> update render node" % ( (time.time() - prevTimer)*1000 ) )
        prevTimer = time.time()

        # Validate dependencies
        self.dispatchTree.validateDependencies()
        if singletonconfig.get('CORE','GET_STATS'):
            singletonstats.theStats.cycleTimers['update_dependencies'] = time.time() - prevTimer
        LOGGER.info("%8.2f ms --> validate dependencies" % ( (time.time() - prevTimer)*1000 ) )
        prevTimer = time.time()


        # update db
        self.updateDB()
        if singletonconfig.get('CORE','GET_STATS'):
            singletonstats.theStats.cycleTimers['update_db'] = time.time() - prevTimer
        LOGGER.info("%8.2f ms --> update DB" % ( (time.time() - prevTimer)*1000 ) )
        prevTimer = time.time()

        # compute and send command assignments to rendernodes
        assignments = self.computeAssignments()
        if singletonconfig.get('CORE','GET_STATS'):
            singletonstats.theStats.cycleTimers['compute_assignment'] = time.time() - prevTimer
        LOGGER.info("%8.2f ms --> compute assignements." % ( (time.time() - prevTimer)*1000)  )
        prevTimer = time.time()

        self.sendAssignments(assignments)
        if singletonconfig.get('CORE','GET_STATS'):
            singletonstats.theStats.cycleTimers['send_assignment'] = time.time() - prevTimer
            singletonstats.theStats.cycleCounts['num_assignments'] = len(assignments)
        LOGGER.info("%8.2f ms --> send %r assignements." % ( (time.time() - prevTimer)*1000, len(assignments) )  )
        prevTimer = time.time()

        # call the release finishing status on all rendernodes
        for renderNode in self.dispatchTree.renderNodes.values():
            renderNode.releaseFinishingStatus()
        if singletonconfig.get('CORE','GET_STATS'):
            singletonstats.theStats.cycleTimers['release_finishing'] = time.time() - prevTimer
        LOGGER.info("%8.2f ms --> releaseFinishingStatus" % ( (time.time() - prevTimer)*1000 ) )
        prevTimer = time.time()

        loopDuration = (time.time() - loopStartTime)*1000
        LOGGER.info( "%8.3f ms --> cycle ended. " % loopDuration )
        LOGGER.info("-----------------------------------------------------")

        # TODO: process average and sums of datas in stats, if flush time, send it to disk
        if singletonconfig.get('CORE','GET_STATS'):
            singletonstats.theStats.cycleTimers['time_elapsed'] = time.time() - loopStartTime
            singletonstats.theStats.aggregate()



    def updateDB(self):

        # TODO: Study how to change the DB subsystem to a simple file dump (json or pickle)

        # data1 = {'a': [1, 2.0, 3, 4],
        #          'b': ('string', u'Unicode string'),
        #          'c': None}
        # with open('/datas/puli/Puli/data.json', 'wb') as fp:
        #     json.dump(self.dispatchTree, fp)

        # import shelve

        # d = shelve.open('/datas/puli/Puli/data.pkl')
        # d['test'] = self.dispatchTree
        # d.close()

        if settings.DB_ENABLE:
            self.pulidb.createElements(self.dispatchTree.toCreateElements)
            self.pulidb.updateElements(self.dispatchTree.toModifyElements)
            self.pulidb.archiveElements(self.dispatchTree.toArchiveElements)
            # LOGGER.info("                UpdateDB: create=%d update=%d delete=%d" % (len(self.dispatchTree.toCreateElements), len(self.dispatchTree.toModifyElements), len(self.dispatchTree.toArchiveElements)) )
        self.dispatchTree.resetDbElements()

    def computeAssignments(self):
        '''Computes and returns a list of (rendernode, command) assignments.'''

        # LOGGER.info("@ -----------------------------------------------------")
        # LOGGER.info("@ COMPUTE ASSIGNMENT")
        # LOGGER.info("@-----------------------------------------------------")

        from .model.node import NoRenderNodeAvailable
        # if no rendernodes available, return
        if not any(rn.isAvailable() for rn in self.dispatchTree.renderNodes.values()):
            return []

        assignments = []

        # first create a set of entrypoints that are not done nor cancelled nor blocked nor paused and that have at least one command ready
        # FIXME: hack to avoid getting the 'graphs' poolShare node in entryPoints, need to avoid it more nicely...
        entryPoints = set([poolShare.node for poolShare in self.dispatchTree.poolShares.values() if poolShare.node.status not in [NODE_BLOCKED, NODE_DONE, NODE_CANCELED, NODE_PAUSED] and poolShare.node.readyCommandCount > 0 and poolShare.node.name != 'graphs'])

        # LOGGER.info("@ - getting entryPoints (%d)" % len(entryPoints))
        # for item in entryPoints:
        #     LOGGER.info("@      -node:%r -pool:%r" % (item.name, item.poolShares.values()) )


        # don't proceed to the calculation if no rns availables in the requested pools
        rnsBool = False
        for pool, nodesiterator in groupby(entryPoints, lambda x: x.poolShares.values()[0].pool):
            rnsAvailables = set([rn for rn in pool.renderNodes if rn.status not in [RN_UNKNOWN, RN_PAUSED, RN_WORKING]])
            if len(rnsAvailables):
                rnsBool = True

        if not rnsBool:
            return []

        # sort by pool for the groupby
        entryPoints = sorted(entryPoints, key=lambda node: node.poolShares.values()[0].pool)

        # update the value of the maxrn for the poolshares (parallel dispatching)
        for pool, nodesiterator in groupby(entryPoints, lambda x: x.poolShares.values()[0].pool):

            # LOGGER.info("@      -foreach group of pool:%r" % (pool.name) )

            # we are treating every active node of the pool
            nodesList = [node for node in nodesiterator]
            # LOGGER.info("@           -nodes:%r" % (nodesList) )


            # the new maxRN value is calculated based on the number of active jobs of the pool, and the number of online rendernodes of the pool
            rnsNotOffline = set([rn for rn in pool.renderNodes if rn.status not in [RN_UNKNOWN, RN_PAUSED]])
            rnsSize = len(rnsNotOffline)
            # LOGGER.info("@           -nb rns awake:%r" % (len(rnsNotOffline)) )

            # if we have a userdefined maxRN for some nodes, remove them from the list and substracts their maxRN from the pool's size
            l = nodesList[:]  # duplicate the list to be safe when removing elements
            for node in l:
                if node.poolShares.values()[0].userDefinedMaxRN and node.poolShares.values()[0].maxRN not in [-1, 0]:
                    nodesList.remove(node)
                    rnsSize -= node.poolShares.values()[0].maxRN

            if len(nodesList) == 0:
                continue
            updatedmaxRN = rnsSize // len(nodesList)
            remainingRN = rnsSize % len(nodesList)

            # sort by id (fifo)
            nodesList = sorted(nodesList, key=lambda x: x.id)

            # then sort by dispatchKey (priority)
            nodesList = sorted(nodesList, key=lambda x: x.dispatchKey, reverse=True)
            for dk, nodeIterator in groupby(nodesList, lambda x: x.dispatchKey):
                nodes = [node for node in nodeIterator]

                # for each priority, if there is only one node, set the maxRN to -1
                if len(nodes) == 1:
                    nodes[0].poolShares.values()[0].maxRN = -1
                    continue
                # else, if a priority has been set, divide the available RNs between the nodes (parallel dispatching)
                elif dk != 0:
                    newmaxRN = rnsSize // len(nodes)
                    newremainingRN = rnsSize % len(nodes)
                    for node in nodes:
                        node.poolShares.values()[0].maxRN = newmaxRN
                        if newremainingRN > 0:
                            node.poolShares.values()[0].maxRN += 1
                            newremainingRN -= 1
                else:
                    for node in nodes:
                        node.poolShares.values()[0].maxRN = updatedmaxRN
                        if remainingRN > 0:
                            node.poolShares.values()[0].maxRN += 1
                            remainingRN -= 1

        # now, we are treating every nodes
        # sort by id (fifo)
        entryPoints = sorted(entryPoints, key=lambda node: node.id)
        # then sort by dispatchKey (priority)
        entryPoints = sorted(entryPoints, key=lambda node: node.dispatchKey, reverse=True)

        ####
        #for entryPoint in entryPoints:
        #    if any([poolShare.hasRenderNodesAvailable() for poolShare in entryPoint.poolShares.values()]):
        #        try:
        #            (rn, cmd) = entryPoint.dispatchIterator()
        ####
        for entryPoint in entryPoints:
            if any([poolShare.hasRenderNodesAvailable() for poolShare in entryPoint.poolShares.values()]):
                try:

                    # LOGGER.debug(">>>>> DBG ITERATOR")
                    # for com in entryPoint.nuIterator():
                    #     print "         %r " % com
                    # for (rn, com) in entryPoint.dispatchIterator(lambda: True):
                    #     print "         %r -> %r" % (com, rn.name)
                    # LOGGER.debug("<<<<< END DBG ITERATOR")

                    for (rn, com) in entryPoint.dispatchIterator(lambda: self.queue.qsize() > 0):
                        assignments.append((rn, com))
                        # increment the allocatedRN for the poolshare
                        poolShare.allocatedRN += 1
                        # save the active poolshare of the rendernode
                        rn.currentpoolshare = poolShare

                except NoRenderNodeAvailable:
                    pass
        assignmentDict = collections.defaultdict(list)
        for (rn, com) in assignments:
            assignmentDict[rn].append(com)

        return assignmentDict.items()

    def updateRenderNodes(self):
        for rendernode in self.dispatchTree.renderNodes.values():
            rendernode.updateStatus()

    def sendAssignments(self, assignmentList):
        '''Processes a list of (rendernode, command) assignments.'''

        def sendAssignment(args):
            rendernode, commands = args
            failures = []
            for command in commands:
                headers = {}
                if not rendernode.idInformed:
                    headers["rnId"] = rendernode.id
                root = command.task
                ancestors = [root]
                while root.parent:
                    root = root.parent
                    ancestors.append(root)
                arguments = {}
                environment = {
                    'PULI_USER': command.task.user,
                    'PULI_ALLOCATED_MEMORY': unicode(rendernode.usedRam[command.id]),
                    'PULI_ALLOCATED_CORES': unicode(rendernode.usedCoresNumber[command.id]),
                }
                for ancestor in ancestors:
                    arguments.update(ancestor.arguments)
                    environment.update(ancestor.environment)
                arguments.update(command.arguments)
                commandDict = {
                    "id": command.id,
                    "runner": str(command.task.runner),
                    "arguments": arguments,
                    "validationExpression": command.task.validationExpression,
                    "taskName": command.task.name,
                    "relativePathToLogDir": "%d" % command.task.id,
                    "environment": environment,
                }
                body = json.dumps(commandDict)
                headers["Content-Length"] = len(body)
                headers["Content-Type"] = "application/json"

                try:
                    resp, data = rendernode.request("POST", "/commands/", body, headers)
                    if not resp.status == 202:
                        LOGGER.error("Assignment request failed: command %d on worker %s", command.id, rendernode.name)
                        failures.append((rendernode, command))
                    else:
                        LOGGER.info("Sent assignment of command %d to worker %s", command.id, rendernode.name)
                except rendernode.RequestFailed, e:
                    LOGGER.exception("Assignment of command %d to worker %s failed: %r", command.id, rendernode.name, e)
                    failures.append((rendernode, command))
            return failures

        requests = makeRequests(sendAssignment, [[a, b] for (a, b) in assignmentList], self._assignmentFailed)
        for request in requests:
            self.threadPool.putRequest(request)

    def _assignmentFailed(self, request, failures):
        for assignment in failures:
            rendernode, command = assignment
            rendernode.clearAssignment(command)
            command.clearAssignment()

            LOGGER.info(" - assignment cleared: command[%r] on rn[%r]" % (command.id, rendernode.name) )


    def handleNewGraphRequestApply(self, graph):
        '''Handles a graph submission request and closes the given ticket
        according to the result of the process.
        '''
        prevTimer = time.time()

        nodes = self.dispatchTree.registerNewGraph(graph)

        LOGGER.info("%.2f ms --> graph registered" % ( (time.time() - prevTimer)*1000 ) )
        prevTimer = time.time()

        # handles the case of post job with paused status
        for node in nodes:
            try:
                if node.tags['paused'] == 'true':
                    node.setPaused(True)
            except KeyError:
                continue

        LOGGER.info("%.2f ms --> jobs set in pause if needed" % ( (time.time() - prevTimer)*1000 ) )
        prevTimer = time.time()

        LOGGER.info('Added graph "%s" to the model.' % graph['name'])
        return nodes

    def updateCommandApply(self, dct):
        '''
        Called from a RN with a json desc of a command (ie rendernode info, command info etc).
        Raise an execption to tell caller to send a HTTP404 response to RN, if not error a HTTP200 will be send instead
        '''
        commandId = dct['id']
        renderNodeName = dct['renderNodeName']

        try:
            command = self.dispatchTree.commands[commandId]
        except KeyError:
            raise KeyError("Command not found: %d" % commandId)

        if not command.renderNode:
            # souldn't we reassign the command to the rn??
            raise KeyError("Command %d (%d) is no longer registered on rendernode %s" % (commandId, int(dct['status']), renderNodeName))
        elif command.renderNode.name != renderNodeName:
            # in this case, kill the command running on command.renderNode.name
            # rn = command.renderNode
            # rn.clearAssignment(command)
            # rn.request("DELETE", "/commands/" + str(commandId) + "/")
            raise KeyError("Command %d is running on a different rendernode (%s) than the one in puli's model (%s)." % (commandId, renderNodeName, command.renderNode.name))

        rn = command.renderNode
        rn.lastAliveTime = max(time.time(), rn.lastAliveTime)

        # if command is no more in the rn's list, it means the rn was reported as timeout or asynchronously removed from RN
        if commandId not in rn.commands:
            if len(rn.commands) == 0 and command.status is not enums.CMD_CANCELED:
                # in this case, re-add the command to the list of the rendernode
                rn.commands[commandId] = command
                # we should re-reserve the lic
                rn.reserveLicense(command, self.licenseManager)
                LOGGER.warning("re-assigning command %d on %s. (TIMEOUT?)" % (commandId, rn.name))
            else:
                # The command has been cancelled on the dispatcher but update from RN only arrives now
                LOGGER.warning("Status update from %d (%d) on %s but command is currently assigned." % (commandId, int(dct['status']), rn.name ))
                pass

        if "status" in dct:
            command.status = int(dct['status'])

        if "completion" in dct and command.status == enums.CMD_RUNNING:
            command.completion = float(dct['completion'])

        command.message = dct['message']

        if "validatorMessage" in dct:
            command.validatorMessage = dct['validatorMessage']
            command.errorInfos = dct['errorInfos']
            if command.validatorMessage:
                command.status = enums.CMD_ERROR

        # Stats info received and not none. Means we need to update it on the command.
        # If stats received is none, no change on the worker, we do not update the command.
        if "stats" in dct and dct["stats"] is not None:
            # LOGGER.debug("Updating stats on server: %r" % dct["stats"] )
            command.stats = dct["stats"]



    def queueWorkload(self, workload):
        self.queue.put(workload)
