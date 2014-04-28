####################################################################################################
# @file command.py
# @package
# @author
# @date 2008/10/29
# @version 0.1
#
# @mainpage
#
####################################################################################################

import time
import logging
from threading import Timer

from octopus.core.enums.command import *
from octopus.core.enums.rendernode import RN_FINISHING
from . import models
from octopus.dispatcher import settings
from octopus.core import singletonconfig

LOGGER = logging.getLogger('command')


class Command(models.Model):

    description = models.StringField()
    task = models.ModelField()
    arguments = models.DictField()
    status = models.IntegerField()
    message = models.StringField()
    stats = models.DictField()       # Stats updated by the runner and stored on server
    completion = models.FloatField()
    renderNode = models.ModelField(allow_null=True)
    creationTime = models.FloatField()
    startTime = models.FloatField(allow_null=True)
    updateTime = models.FloatField(allow_null=True)
    endTime = models.FloatField(allow_null=True)
    nbFrames = models.IntegerField(allow_null=True)
    avgTimeByFrame = models.FloatField(allow_null=True)
    retryCount = models.IntegerField()
    retryRnList = models.ListField()

    def __init__(self, id, description, task, arguments, status=CMD_READY, completion=None, renderNode=None, creationTime=None, startTime=None, updateTime=None, endTime=None, stats={}, message=""):

        from octopus.dispatcher.model import Task
        models.Model.__init__(self)
        if task:
            assert isinstance(task, Task)
        if id is None:
            self.id = None
            self.description = str(description)
            self.task = task
            self.arguments = arguments
            self.stats = stats
            self.status = int(status)
            self.completion = 0.
            self.renderNode = None
            self.creationTime = time.time()
            self.startTime = None
            self.updateTime = None
            self.endTime = None
        else:
            # id exists, so we'll assume that status, completion and creationTime exist as well
            assert status is not None
            assert completion is not None
            assert creationTime is not None
            self.id = int(id)
            self.description = str(description)
            self.task = task
            self.arguments = arguments
            self.stats = stats
            self.status = int(status)
            self.completion = float(completion)
            self.renderNode = renderNode
            self.creationTime = creationTime
            self.updateTime = updateTime
            self.startTime = startTime
            self.endTime = endTime
        self.message = str(message)
        # compute the average time by frame
        self.computeAvgTimeByFrame()
        self.retryCount = 0
        self.retryRnList = []

    def __repr__(self):
        return "Command(id=%r, status=%s)" % (self.id, CMD_STATUS_NAME[self.status])

    def __str__(self):
        return "Command(id=%r, status=%s, desc=%s, task=%r, completion=%.2f, RN=%s)" % (self.id, CMD_STATUS_NAME[self.status], 
            self.description, str(self.task), self.completion, self.renderNode)

    def clearAssignment(self):
        self.renderNode = None
        self.startTime = None
        self.updateTime = None
        self.endTime = None
        self.status = CMD_READY

    def assign(self, renderNode):
        self.renderNode = renderNode
        self.startTime = time.time()
        self.status = CMD_ASSIGNED

    def cancel(self):
        """
        TOFIX log message if RN clearing failed !!!
        """

        if self.status in (CMD_FINISHING, CMD_DONE, CMD_CANCELED):
            return
        elif self.status == CMD_RUNNING:
            try:
                self.renderNode.clearAssignment(self)
                (response, data) = self.renderNode.request("DELETE", "/commands/" + str(self.id) + "/")
                LOGGER.info( "data: %r" % data )
                
            except Exception:
                # if request has failed, it means the rendernode is unreachable
                self.status = CMD_CANCELED
        elif self.renderNode is not None:
            self.renderNode.clearAssignment(self)
        self.status = CMD_CANCELED


    def setDoneStatus(self):
        """

        FIXME c'est pas bon, il faut que la demande soit envoyee au RN directement qui va forcer le done...
        """
        if self.status in (CMD_FINISHING, CMD_DONE, CMD_CANCELED):
            return
        elif self.status == CMD_RUNNING:
            pass
            try:
                self.renderNode.clearAssignment(self)
                (response, data) = self.renderNode.request("POST", "/commands/" + str(self.id) + "/done")
                LOGGER.debug( "data: %r" % data )
            except Exception:
                # if request has failed, it means the rendernode is unreachable
                LOGGER.warning("Impossible to cancel command %d on the RN: %s" % (self.id, self.renderNode.name ))
                self.status = CMD_CANCELED
        elif self.renderNode is not None:
            self.renderNode.clearAssignment(self)
        self.status = CMD_DONE

    def setReadyAndKill(self):
        if self.renderNode is not None:
            self.renderNode.request("DELETE", "/commands/" + str(self.id) + "/")
            # FIXME test the return value of this request ?
            self.renderNode.reset()
        self.setReadyStatusAndClear()

    def setReadyStatus(self):
        if isRunningStatus(self.status):
            raise RuntimeError("Cannot reset a running command.")
        self.setReadyStatusAndClear()

    def setReadyStatusAndClear(self):
        self.status = CMD_READY
        self.clearAssignment()
        self.completion = 0.0
        self.message = ""

    def computeAvgTimeByFrame(self):
        # compute the nbFrames
        self.nbFrames = 0
        self.avgTimeByFrame = 0.0
        descTab = self.description.split('_')
        try:
            self.nbFrames = int(descTab[-1]) - int(descTab[-2]) + 1
        except ValueError:
            pass
        except IndexError:
            pass
        # compute the average time by frame if the command is done
        if self.nbFrames != 0 and self.startTime is not None and self.endTime is not None and self.status == 5:
            totalTime = self.endTime - self.startTime
            self.avgTimeByFrame = (1000 * totalTime) / self.nbFrames
            if self.task:
                for node in self.task.nodes.values():
                    # if the node has a parent e.g we are in a FolderNode, we set the avgtime on the FolderNode as well
                    if node.parent and node.parent.id != 1:
                        self.appendAvgTimeByFrameToNode(node.parent)
                    self.appendAvgTimeByFrameToNode(node)

    def appendAvgTimeByFrameToNode(self, node):
        node.averageTimeByFrameList.append(self.avgTimeByFrame)
        node.averageTimeByFrame = sum(node.averageTimeByFrameList) / len(node.averageTimeByFrameList)
        node.minTimeByFrame = min(node.averageTimeByFrameList)
        node.maxTimeByFrame = max(node.averageTimeByFrameList)

    def finish(self):
        "Called on a finished command, it sets the endTime for this command and releases the resources on its associated render node"
        if not isFinalStatus(self.status):
            raise ValueError("cannot finish a command that is not completed")
        if self.renderNode is None:
            return
        self.renderNode.unassign(self)

    def to_json(self):
        jsonRepr = super(Command, self).to_json()
        if self.renderNode is not None:
            renderNodeName = self.renderNode.name
            jsonRepr['renderNode'] = renderNodeName
        else:
            jsonRepr['renderNode'] = None
        return jsonRepr


class CommandDatesUpdater(object):

    def onCreationEvent(self, obj):
        pass

    def onDestructionEvent(self, obj):
        pass

    def onChangeEvent(self, obj, field, oldvalue, newvalue):
        if field == 'completion':
            self.onCompletionUpdate(obj)
        elif field == 'status':
            self.onStatusUpdate(obj)

    def onCompletionUpdate(self, cmd):
        cmd.updateTime = time.time()

    def onStatusUpdate(self, cmd):
        cmd.updateTime = time.time()
        # append the command's status to the rendernode's history
        if isFinalStatus(cmd.status):
            # only if we don't already have a command for this task
            if hasattr(cmd.renderNode, 'tasksHistory') and cmd.task.id not in cmd.renderNode.tasksHistory:
                cmd.renderNode.tasksHistory.append(cmd.task.id)
                cmd.renderNode.history.append(cmd.status)
        if cmd.status is CMD_DONE:
            # TOFIX: handle CANCEL status and update end time when cancelling a 
            # job so that it can be properly cleaned
            cmd.endTime = cmd.updateTime
            cmd.computeAvgTimeByFrame()
        # autoretry
        elif cmd.status is CMD_ERROR:
            if cmd.retryCount == singletonconfig.get('CORE','MAX_RETRY_CMD_COUNT'):
                cmd.retryRnList.append(cmd.renderNode.name)
            elif cmd.retryCount < singletonconfig.get('CORE','MAX_RETRY_CMD_COUNT'):
                t = Timer(singletonconfig.get('CORE','DELAY_BEFORE_AUTORETRY'), self.autoretry, [cmd])
                t.start()
        elif cmd.status is CMD_ASSIGNED:
            cmd.startTime = cmd.updateTime
        elif cmd.status < CMD_ASSIGNED:
            cmd.startTime = None

    def autoretry(self, cmd):
        rn = cmd.renderNode
        cmd.retryRnList.append(rn.name)
        cmd.setReadyStatusAndClear()
        rn.clearAssignment(cmd)
        rn.status = RN_FINISHING
        cmd.retryCount += 1


Command.changeListeners.append(CommandDatesUpdater())
