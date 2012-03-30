####################################################################################################
# @file rendernode.py
# @package dispatcher.model
# @author 
# @date 2008/10/29
# @version 0.1
#
# @mainpage
# 
####################################################################################################

import httplib as http
import time
import logging
import errno

from octopus.dispatcher.model.enums import *

from . import models

LOGGER = logging.getLogger('dispatcher.webservice')

# set the status of a render node to RN_UNKNOWN after TIMEOUT secondes have elapsed after last update
TIMEOUT = 60.0

## This class represents the state of a RenderNode.
#
class RenderNode(models.Model):

    name = models.StringField()
    coresNumber = models.IntegerField()
    freeCoresNumber = models.IntegerField()
    usedCoresNumber = models.DictField(as_item_list=True)
    ramSize = models.IntegerField()
    freeRam = models.IntegerField()
    usedRam = models.DictField(as_item_list=True)
    speed = models.FloatField()
    commands = models.ModelDictField()
    status = models.IntegerField()
    host = models.StringField()
    port = models.IntegerField()
    pools = models.ModelListField(indexField='name')
    caracteristics = models.DictField()
    isRegistered = models.BooleanField()
    lastAliveTime = models.FloatField()


    def __init__(self, id, name, coresNumber, speed, ip, port, ramSize, caracteristics=None):
        '''Constructs a new Rendernode.
        
        :parameters:
        - `name`: the name of the rendernode
        - `coresNumber`: the number of processors
        - `speed`: the speed of the processor
        '''
        self.id = int(id) if id else None
        self.name = str(name)

        self.coresNumber = int(coresNumber)
        self.ramSize = int(ramSize)
        self.licenceManager = None
        self.freeCoresNumber = int(coresNumber)
        self.usedCoresNumber = {}
        self.freeRam = int(ramSize)
        self.usedRam = {}

        self.speed = speed
        # @change: self.commands replace self.command and allows multiple commands
        # on the same rendernode. Indexed by command.id
        # @todo: remove that change note.
        self.commands = {}
        self.status = RN_UNKNOWN
        self.responseId = None
#        try:
#            self.host = socket.gethostbyname(self.name.split('.')[0])
#        except:
        self.host = str(ip)
        self.port = int(port)
        self.pools = []
#        self.poolId = 0
        self.idInformed = False
        self.isRegistered = False
        self.lastAliveTime = 0
        self.httpConnection = None
        self.caracteristics = caracteristics if caracteristics else {}
        self.currentpoolshare = None

        if not "softs" in self.caracteristics:
            self.caracteristics["softs"] = []


    ## Returns True if this render node is available for command assignment.
    #
    def isAvailable(self):
        # at least one core is needed to do a job
        return (self.isRegistered and self.status >= RN_IDLE and self.freeCoresNumber)

    def release(self):
        self.status = RN_IDLE
        self.reset()
    
    def reset(self):
        self.commands = {}
        self.freeCoresNumber = int(self.coresNumber)
        self.usedCoresNumber = {}
        self.freeRam = int(self.ramSize)
        self.usedRam = {}

    ## Returns a human readable representation of this RenderNode.
    #
    def __repr__(self):
        return u'RenderNode(id=%s, name=%s, host=%s, port=%s)' % (repr(self.id), repr(self.name), repr(self.host), repr(self.port))


    ## Clears all of this rendernode's fields related to the specified assignment.
    #
    def clearAssignment(self, command):
        '''Removes command from the list of commands assigned to this rendernode.'''
        # in case of failed assignment, decrement the allocatedRN value
        if self.currentpoolshare:
            self.currentpoolshare.allocatedRN -= 1
            self.currentpoolshare = None
        try:
            del self.commands[command.id]
        except KeyError:
            LOGGER.debug('attempt to clear assignment of not assigned command %d on worker %s', command.id, self.name)
        else:
            self.releaseRessources(command)
            self.releaseLicence(command)


    ## Add a command assignment 
    #    
    def addAssignment(self, command):
        assert not command.id in self.commands
        self.commands[command.id] = command
        self.updateStatus()


    ## Reserve ressource
    # @todo: check
    def reserveRessources(self, command):
        res = min(self.freeCoresNumber, command.task.maxNbCores) or self.freeCoresNumber
        self.usedCoresNumber[command.id] = res
        self.freeCoresNumber -= res


        res = min(self.freeRam, command.task.ramUse) or self.freeRam

        self.usedRam[command.id] = res
        self.freeRam -= res


    ## Release licence
    # @todo: check
    def releaseLicence(self, command):
        licence = command.task.licence
        if licence:
            self.licenceManager.releaseLicenceForRenderNode(licence, self)


    ## Release ressource
    # @todo: check
    def releaseRessources(self, command):
        res = self.usedCoresNumber[command.id]
        self.freeCoresNumber += res
        del self.usedCoresNumber[command.id]

        res = self.usedRam[command.id]
        self.freeRam += res
        del self.usedRam[command.id]


    ## Unassign a finished command
    #
    def unassign(self, command):
        if not isFinalStatus(command.status):
            raise ValueError("cannot unassign unfinished command %s" % repr(command))
        self.clearAssignment(command)
        self.updateStatus()

    def remove(self):
        self.fireDestructionEvent(self)

    ## update node status according to its commands ones
    #  status is not changed if no info is brought by the commands
    #
    def updateStatus(self):
        if time.time() > (self.lastAliveTime + TIMEOUT):
            # timeout the commands running on this node
            if RN_UNKNOWN != self.status:
                LOGGER.info("rendernode %s is not responding", self.name)
                self.status = RN_UNKNOWN
                if self.commands:
                    for command in self.commands.values():
                        command.status = CMD_TIMEOUT
            return
        if not self.commands and self.status not in (RN_PAUSED, RN_BOOTING) :
            self.status = RN_IDLE
            # Actually, I think this is necessary in case of a cancel command
            if self.currentpoolshare:
                self.currentpoolshare.allocatedRN -= 1
                self.currentpoolshare = None
            return
        commandStatus = [command.status for command in self.commands.values()]
        if CMD_RUNNING in commandStatus:
            self.status = RN_WORKING
        elif CMD_ERROR in commandStatus:
            self.status = RN_WORKING
        elif CMD_FINISHING in commandStatus:
            self.status = RN_FINISHING
        elif CMD_ASSIGNED in commandStatus:
            self.status = RN_ASSIGNED
        elif self.status == RN_UNKNOWN:
            self.status = RN_IDLE
        elif CMD_DONE in commandStatus:
            self.status = RN_FINISHING # was RN_IDLE // modified by acs : pour resoudre le probleme de priorites des jobs
        elif self.status not in (RN_IDLE, RN_BOOTING, RN_UNKNOWN, RN_PAUSED):
            LOGGER.error("Unable to compute new status for rendernode %r (status %r, commands %r)", self, self.status, self.commands)


    ## releases the finishing status of the rendernodes
    #
    def releaseFinishingStatus(self):
        if self.status in (RN_FINISHING, RN_BOOTING):
            print "releaseFinishingStatus : %s, %s" % (self.name, self.status)
            self.status = RN_IDLE
            if self.currentpoolshare:
                self.currentpoolshare.allocatedRN -= 1
                self.currentpoolshare = None
    ##
    #
    # @warning The returned HTTPConnection is not safe to use from multiple threads
    #
    def  getHTTPConnection(self):
        return http.HTTPConnection(self.host, self.port)
#        if (self.httpConnection == None or 
#            self.httpConnection.port!=self.port or 
#            self.httpConnection.host!=self.host
#        ):
#            self.httpConnection = http.HTTPConnection(self.host, self.port)
#        return self.httpConnection


    ## An exception class to report a render node http request failure.
    #
    class RequestFailed(Exception):
        pass


    ## Sends a HTTP request to the render node and returns a (HTTPResponse, data) tuple on success.
    #        
    # This method tries to send the request at most settings.RENDERNODE_REQUEST_MAX_RETRY_COUNT times,
    # waiting settings.RENDERNODE_REQUEST_DELAY_AFTER_REQUEST_FAILURE seconds between each try. It
    # then raises a RenderNode.RequestFailed exception.
    #
    # @param method the HTTP method for this request
    # @param url the requested URL
    # @param headers a dictionary with string-keys and string-values (empty by default)
    # @param body the string body for this request (None by default)
    # @raise RenderNode.RequestFailed if the request fails.
    # @note it is a good idea to specify a Content-Length header when giving a non-empty body.
    # @see dispatcher.settings the RENDERNODE_REQUEST_MAX_RETRY_COUNT and 
    #                          RENDERNODE_REQUEST_DELAY_AFTER_REQUEST_FAILURE settings affect 
    #                          the execution of this method.
    #
    def request(self, method, url, body=None, headers={}):
        from octopus.dispatcher import settings

        conn = self.getHTTPConnection()
        # try to process the request at most RENDERNODE_REQUEST_MAX_RETRY_COUNT times.
        for i in xrange(settings.RENDERNODE_REQUEST_MAX_RETRY_COUNT):
            try:
                conn.request(method, url, body, headers)
                response = conn.getresponse()
                if response.length:
                    data = response.read(response.length)
                else:
                    data = None
                # request succeeded
                conn.close()
                return (response, data)
            except http.socket.error, e:
                try:
                    conn.close()
                except:
                    pass
                if e in (errno.ECONNREFUSED, errno.ENETUNREACH):
                    raise self.RequestFailed(cause=e)
            except http.HTTPException, e:
                try:
                    conn.close()
                except:
                    pass
                LOGGER.exception("rendernode.request failed")
            # request failed so let's sleep for a while
            time.sleep(settings.RENDERNODE_REQUEST_DELAY_AFTER_REQUEST_FAILURE)
        # request failed too many times so report a failure
        raise self.RequestFailed()


    def reserveLicence(self, command, licenceManager):
        self.licenceManager = licenceManager
        licence = command.task.licence
        if not licence:
            return True
        return licenceManager.reserveLicenceForRenderNode(licence, self)


    def canRun(self, command):
        for (requirement, value) in command.task.requirements.items():
            if requirement.lower() == "softs": # todo
                for soft in value:
                    if not soft in self.caracteristics['softs']:
                        return False
            else:
                if not requirement in self.caracteristics:
                    return False
                else:
                    caracteristic = self.caracteristics[requirement]
                    if type(caracteristic) != type(value) and not isinstance(value, list):
                        return False
                    if isinstance(value, list) and len(value) == 2:
                        a, b = value
                        if type(a) != type(b) or type(a) != type(caracteristic):
                            return False
                        try:
                            if not (a < caracteristic < b):
                                return False
                        except ValueError:
                            return False
                    else:
                        if isinstance(caracteristic, bool) and caracteristic != value:
                            return False
                        if isinstance(caracteristic, basestring) and caracteristic != value:
                            return False
                        if isinstance(caracteristic, int) and caracteristic < value:
                            return False

        if command.task.minNbCores:
            if self.freeCoresNumber < command.task.minNbCores:
#                LOGGER.debug(self.name + " has not enough ressources (%s instead of %s required)" % (str(self.freeCoresNumber), str(command.task.minNbCores)))
                return False
        else:
            if self.freeCoresNumber != self.coresNumber:
#                LOGGER.debug(self.name + " has not enough ressources. All cores should be free.")
                return False


        if self.freeRam < command.task.ramUse:
#            LOGGER.debug(self.name + " has not enough ram (%s instead of %s required)" % (str(self.freeRam), str(command.task.ramUse)))
            return False

        return True
