import os
from Queue import Queue
try:
    import simplejson as json
except ImportError:
    import json
import logging

from octopus.core.communication.http import Http400, Http404
from octopus.worker import settings
from tornado.web import Application, RequestHandler

# /commands/ [GET] { commands: [ { id, status, completion } ] }
# /commands/ [POST] { id, jobtype, arguments }
# /commands/{id}/ [GET] { id, status, completion, jobtype, arguments }
# /commands/{id}/ [DELETE] stops the job
# /online/ [GET] { online }
# /online/ [SET] { online }
# /status/ [GET] { status, ncommands, globalcompletion }

LOGGER = logging.getLogger("workerws")


class WorkerWebService(Application):

    def __init__(self, framework, port):
        super(WorkerWebService, self).__init__([
            (r'/commands/?$', CommandsResource, dict(framework=framework)),
            (r'/commands/(?P<id>\d+)/?$', CommandResource, dict(framework=framework)),
            (r'/debug/?$', DebugResource, dict(framework=framework)),
            (r'/log/?$', WorkerLogResource),
            (r'/log/command/(?P<path>\S+)', CommandLogResource),
            (r'/updatesysinfos/?$', UpdateSysResource, dict(framework=framework)),
            (r'/pause/?$', PauseResource, dict(framework=framework))
        ])
        self.queue = Queue()
        self.listen(port, "0.0.0.0")
        self.framework = framework
        self.port = port


class BaseResource(RequestHandler):
    def initialize(self, framework):
        self.framework = framework
        self.rnId = None

    def setRnId(self, request):
        if self.rnId == None and "rnId" in request.headers:
            self.rnId = request.headers['rnId']

    def getBodyAsJSON(self):
        try:
            return json.loads(self.request.body)
        except:
            return Http400("The HTTP body is not a valid JSON object")


class PauseResource(BaseResource):
    def post(self):
        self.setRnId(self.request)
        data = self.getBodyAsJSON()
        #LOGGER.warning(data)
        content = data["content"]
        killfile = settings.KILLFILE
        if os.path.isfile(killfile):
            os.remove(killfile)
        # if 0, unpause the worker
        if content != "0":
            if not os.path.isdir(os.path.dirname(killfile)):
                os.makedirs(os.path.dirname(killfile))
            f = open(killfile, 'w')
            # if -1, kill all current rendering processes
            # if -2, schedule the worker for a restart
            # if -3, kill all and schedule for restart
            if content in ["-1", "-2", "-3"]:
                f.write(content)
            f.close()
        self.set_status(202)


class CommandsResource(BaseResource):
    def get(self):
        '''Lists the commands running on this worker.'''
        commands = [{
            'id': command.id,
            'status': command.status,
            'completion': command.completion,
            'message': command.message,
        } for command in self.framework.application.commands.values()]
        self.write({'commands': commands})

    def post(self):
        # @todo this setRnId call may be just in doOnline necessary
        self.setRnId(self.request)
        data = self.getBodyAsJSON()
        dct = {}
        for key, value in data.items():
            dct[str(key)] = value
        dct['commandId'] = int(dct['id'])
        del dct['id']
        self.framework.addOrder(self.framework.application.addCommandApply, **dct)
        self.set_status(202)


class CommandResource(BaseResource):
    def put(self, id):
        self.setRnId(self.request)
        rawArgs = self.getBodyAsJSON()
        if 'status' in rawArgs or 'completion' in rawArgs:
            args = {
                'commandId': int(id),
                'status': rawArgs.get('status', None),
                'message': rawArgs.get('message', None),
                'completion': rawArgs.get('completion', None)
            }
            self.framework.addOrder(self.framework.application.updateCommandApply, **args)
        else:
            # validator message case
            args = {
                'commandId': int(id),
                'validatorMessage': rawArgs.get('validatorMessage', None),
                'errorInfos': rawArgs.get('errorInfos', None)
            }
            self.framework.addOrder(self.framework.application.updateCommandValidationApply, **args)
        self.set_status(202)

    def delete(self, id):
        dct = {'commandId': int(id)}
        self.framework.addOrder(self.framework.application.stopCommandApply, **dct)
        self.set_status(202)


class DebugResource(BaseResource):
    def get(self):
        watchers = self.framework.application.commandWatchers.values()
        content = [{'id': watcher.command.id} for watcher in watchers]
        self.write(content)


class WorkerLogResource(RequestHandler):
    def get(self):
        logFilePath = os.path.join(settings.LOGDIR, "worker.log")
        if os.path.isfile(logFilePath):
            logFile = open(logFilePath, 'r')
            logFileContent = logFile.read()
            logFile.close()
            self.set_header('Content-Type', 'text/plain')
            self.write(logFileContent)
        return Http404('no log file')


class CommandLogResource(RequestHandler):
    def get(self, path):
        logFilePath = os.path.join(settings.LOGDIR, path)
        if os.path.isfile(logFilePath):
            logFile = open(logFilePath, 'r')
            logFileContent = logFile.read()
            logFile.close()
            self.set_header('Content-Type', 'text/plain')
            self.write(logFileContent)
        return Http404('no log file')


class UpdateSysResource(BaseResource):
    def get(self):
        args = {}
        self.framework.addOrder(self.framework.application.updateSysInfos, **args)
