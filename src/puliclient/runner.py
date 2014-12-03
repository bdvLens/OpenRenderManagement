#!/usr/bin/python
# -*- coding: utf8 -*-
"""
"""
__author__ = "Jerome Samson"
__copyright__ = "Copyright 2014, Mikros Image"

import site
import os
import sys
import traceback

import threading
import subprocess

try:
    import simplejson as json
except ImportError:
    import json

from puliclient.jobs import JobTypeImportError
from puliclient.jobs import CommandError
from puliclient.jobs import TimeoutError

from puliclient.jobs import CommandRunner
from puliclient.jobs import StringParameter


class CallableRunner(CommandRunner):
    execType = StringParameter(mandatory=True)

    def execute(self, arguments, updateCompletion, updateMessage, updateStats, updateLicense):
        self.log.debug("Callable runner arguments: %s" % arguments)

        execType = arguments['execType']

        #
        # Add locations to site path if needed
        #
        # TODO instead, we should start rez with a script to load the callable
        try:
            self.log.info("Add %d site dirs" % len(arguments.get('sysPath', 0)))
            for path in arguments.get('sysPath', None):
                site.addsitedir(path)
        except Exception, e:
            raise JobTypeImportError(e)

        #
        # Retrieve user_args and user_kwargs
        #
        try:
            user_args = json.loads(arguments.get("user_args", None))
            user_kwargs = json.loads(arguments.get("user_kwargs", None))
            self.log.info("args = %s" % user_args)
            self.log.info("kwargs = %s" % user_kwargs)
        except Exception, e:
            print("Problem retrieving args and kwargs: %s)" % e)
            raise CommandError("Problem retrieving args and kwargs: (%s, %s)" % (user_args, user_kwargs))

        #
        # Execute simple function
        #
        if execType == 'function':

            try:
                moduleName = arguments['moduleName']
                funcName = arguments['funcName']
            except Exception:
                raise JobTypeImportError("Missing function or module name in callable arguments: %s", arguments)

            self.log.info("Preparing to load: %s from %s" % (funcName, moduleName))

            #
            # Import module with given moduleName and funcName
            # NB: replace with import_lib.import in python2.7
            #
            try:
                module = __import__(moduleName, fromlist=funcName)
            except ImportError, error:
                traceback.print_exc()
                raise JobTypeImportError("No module '%s' on PYTHONPATH:\n%s. (%s)" % (moduleName, "\n".join(sys.path), error))

            #
            # Load target function in module
            #
            try:
                func = getattr(module, funcName)
            except Exception, e:
                raise JobTypeImportError("No function '%s' defined in module %s (%s)" % (funcName, moduleName, e))

            #
            # Go!
            #
            try:
                func(*user_args, **user_kwargs)
            except Exception, e:
                print "Problem when executing: %s (msg: %s)" % (func, e)
                raise CommandError("Problem when executing: %s (msg: %s)" % (func, e))

        #
        # Execute instance method
        #
        elif execType == 'method':
            self.log.debug("input: %s" % arguments)
            try:
                moduleName = arguments['moduleName']
                className = arguments['className']
                methodName = arguments['methodName']
            except Exception:
                raise JobTypeImportError("Missing function or module name in callable arguments: %s", arguments)

            self.log.info("Preparing to load: %s.%s from %s" % (methodName, className, moduleName))

            module = __import__(moduleName, fromlist=[className])
            jobtype = getattr(module, className)
            func = jobtype()

            #
            # Go!
            #
            try:
                # params = json.loads(arguments.get("params", None))
                # self.log.info("params: %s" % params)
                getattr(func, methodName)(*user_args, **user_kwargs)
            except Exception, e:
                print "Problem when executing: %s (error: %s)" % (func, e)
                raise CommandError("Problem when executing: %s" % func)

        else:
            raise CommandError("Callable not supported")


class RunnerToolkit(object):
    '''
    '''

    @classmethod
    def executeWithTimeout(cls, cmdArgs, env, timeout):
        process = None

        def target():
            os.umask(2)
            process = subprocess.Popen(cmdArgs, env=env)
            process.communicate()

        thread = threading.Thread(target=target)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            process.terminate()
            thread.join()
            raise TimeoutError("Execution has taken more than allowed time (%d)" % timeout)

    def executeWithOutput(cls, cmdArgs, env):

        # TEMPLATE EXECUTION AVEC PARSING DU LOG (source MTOA_DDD)
        out = subprocess.Popen(cmdArgs, stderr=subprocess.STDOUT, bufsize=0, env=env)
        rc = None
        while rc is None:
            line = out.stdout.readline()
            if not line:
                break
            print line,
            sys.stdout.flush()
            # fcp = frameCompletionPattern.search(line)
            # if fcp:
            #     framecomp = float(fcp.group(1).strip())
            #     fc = float(framecomp / 100) / float(totalFrames)
            #     updateCompletion(comp + fc)
            rc = out.poll()

        out.communicate()
        rc = out.poll()
        print '===================================================='
        print '  kick command returns with code %d' % rc
        print '===================================================='

        if rc != 0:
            print 'command failed...'
            raise Exception('  Kick command failed...')
