# $Header: /tmp/libdirac/tmp.stZoy15380/dirac/DIRAC3/DIRAC/Core/Base/Script.py,v 1.8 2008/04/17 07:04:15 rgracian Exp $
__RCSID__ = "$Id: Script.py,v 1.8 2008/04/17 07:04:15 rgracian Exp $"

import sys
import os.path
from DIRAC.ConfigurationSystem.Client.LocalConfiguration import LocalConfiguration
from DIRAC.LoggingSystem.Client.Logger import gLogger
from DIRAC.MonitoringSystem.Client.MonitoringClient import gMonitor

localCfg = LocalConfiguration()

scriptName = False

def parseCommandLine( script = False, ignoreErrors = False, initializeMonitor = True ):
  global localCfg, scriptName

  scriptName = script
  if not scriptName:
    scriptName = os.path.basename( sys.argv[0] )
  scriptSection = localCfg.setConfigurationForScript( scriptName )
  localCfg.addMandatoryEntry( "/DIRAC/Setup" )
  resultDict = localCfg.loadUserData()
  if not ignoreErrors and not resultDict[ 'OK' ]:
    gLogger.error( "There were errors when loading configuration", resultDict[ 'Message' ] )
    sys.exit(1)

  if initializeMonitor:
    gMonitor.setComponentType( gMonitor.COMPONENT_SCRIPT )
    gMonitor.setComponentName( scriptName )
    gMonitor.setComponentLocation( "script" )
    gMonitor.initialize()

def registerSwitch( showKey, longKey, helpString, callback = False ):
  global localCfg
  localCfg.registerCmdOpt( showKey, longKey, helpString, callback )

def getPositionalArgs():
  global localCfg
  return localCfg.getPositionalArguments()

def getUnprocessedSwitches():
  global localCfg
  return localCfg.getUnprocessedSwitches()
