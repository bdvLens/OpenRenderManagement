import os


def getLocalAddress():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('0.0.0.0', 56))
        return s.getsockname()[0]
    except:
        return "127.0.0.1"


def loadSettingsFile(filename):
    g = {}
    l = {}
    execfile(filename, g, l)
    settings = ((name, value) for name, value in l.items() if name.isupper() and  name in globals())
    globals().update(settings)


## SETTINGS ###########################################################

DEBUG = False

PORT = 8004
ADDRESS = getLocalAddress()
RUN_AS = None

# TEST/PROD ENV
#LOGDIR = "/opt/puli/logs"
#CONFDIR = "/opt/puli/conf"

# DEV ENV
LOGDIR = "/s/apps/lin/eval/puli/distrib/OpenRenderManagement/logs"
CONFDIR = "/s/apps/lin/eval/puli/distrib/OpenRenderManagement/Puli/etc/puli"

PIDFILE = "dispatcher.pid"


#
# COMUNICATION BEHAVIOUR
# Defines how the dispatcher will handle communication with a render node
RENDERNODE_REQUEST_MAX_RETRY_COUNT = 10                 # nb of retry for a failed request
RENDERNODE_REQUEST_DELAY_AFTER_REQUEST_FAILURE = .5     # wait 500ms before resending a request in case of failure
RN_TIMEOUT = 1200.0                                     # wait 20 min before considering a render node as offline


#
# CORE BEHAVIOUR
#
# Delay in millisecond between two runs of the "main" iteration: i.e. update db, compute assignement, send orders...
MASTER_UPDATE_INTERVAL = 4000                           


#
# PERSISTENCE MECANISM
#
POOLS_BACKEND_TYPE = "db"
#POOLS_BACKEND_TYPE = "file"
#POOLS_BACKEND_TYPE = "ws"

FILE_BACKEND_RN_PATH = os.path.join(CONFDIR, "workers.lst")
FILE_BACKEND_LICENCES_PATH = os.path.join(CONFDIR, "licences.lst")
FILE_BACKEND_POOL_PATH = os.path.join(CONFDIR, "pools")
WS_BACKEND_URL = ""
WS_BACKEND_PORT = 11800

DB_ENABLE = True
DB_CLEAN_DATA = False

DB_URL = "mysql://puliuser:0ct0pus@127.0.0.1/pulidb"
#DB_URL = "sqlite:///path/to/my/database/file.db"


#
# AUTO RETRY MECANISM
#
MAX_RETRY_CMD_COUNT = 0 # Deactivate autoretry
DELAY_BEFORE_AUTORETRY = 20.0


#
# QUARANTINE
#
# A node that returns more than RN_NB_ERRORS_TOLERANCE will automatically be set in "quarantine" mode.
# This is to prevent hardware/system failure on a particular render node to propagate errors through the whole queue
RN_NB_ERRORS_TOLERANCE = 5
