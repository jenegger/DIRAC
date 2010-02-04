########################################################################
# $HeadURL$
########################################################################

"""  FTS Submit Agent takes files from the TransferDB and submits them to the FTS
"""
from DIRAC  import gLogger, gConfig, S_OK, S_ERROR
from DIRAC.Core.Base.AgentModule import AgentModule
from DIRAC.Core.Utilities.List import breakListIntoChunks
from DIRAC.ConfigurationSystem.Client.PathFinder import getDatabaseSection
from DIRAC.DataManagementSystem.DB.TransferDB import TransferDB
from DIRAC.Core.DISET.RPCClient import RPCClient
from DIRAC.Core.Utilities.Shifter import setupShifterProxyInEnv
from DIRAC.DataManagementSystem.Client.ReplicaManager import ReplicaManager
from DIRAC.DataManagementSystem.Client.DataLoggingClient import DataLoggingClient
import os,time,re
from types import *

__RCSID__ = "$Id$"

class FTSRegisterAgent(AgentModule):

  def initialize(self):

    self.TransferDB = TransferDB()
    self.ReplicaManager = ReplicaManager()
    self.DataLog = DataLoggingClient()

    self.useProxies = self.am_getOption('UseProxies','True').lower() in ( "y", "yes", "true" )
    self.proxyLocation = self.am_getOption('ProxyLocation', '' )
    if not self.proxyLocation:
      self.proxyLocation = False

    if self.useProxies:
      self.am_setModuleParam('shifterProxy','DataManager')
      self.am_setModuleParam('shifterProxyLocation',self.proxyLocation)

    return S_OK()

  def execute(self):

    res = self.TransferDB.getCompletedReplications()
    if not res['OK']:
      gLogger.error("FTSRegisterAgent.execute: Failed to get the completed replications from TransferDB.",res['Message'])
      return S_OK()

    res = self.TransferDB.getWaitingRegistrations()
    if not res['OK']:
      gLogger.error("FTSRegisterAgent.execute: Failed to get waiting registrations from TransferDB.",res['Message'])
      return S_OK()
    lfns = {}
    replicaTuples = []
    for fileID,channelID,lfn,pfn,se in res['Value']:
      if lfns.has_key(lfn):
        continue
      lfns[lfn] = (channelID,fileID,se)
      replicaTuples.append((lfn,pfn,se))

    if replicaTuples:
      gLogger.info("FTSRegisterAgent.execute: Found  %s waiting replica registrations." % len(replicaTuples))
      replicaTupleChunks = breakListIntoChunks(replicaTuples,100)
      gLogger.info("FTSRegisterAgent.execute: Attempting in %s chunks." % len(replicaTupleChunks))
      chunk = 1
      for replicaChunk in replicaTupleChunks:
        gLogger.info("FTSRegisterAgent.execute: Attempting chunk %s." % chunk)
        chunk += 1
        res = self.ReplicaManager.registerReplica(replicaChunk)
        if not res['OK']:
          gLogger.error("FTSRegisterAgent.execute: Completely failed to regsiter replicas.",res['Message'])
          return S_OK()
        for lfn in res['Value']['Successful'].keys():
          channelID,fileID,se = lfns[lfn]
          self.TransferDB.setRegistrationDone(channelID,fileID)
          self.DataLog.addFileRecord(lfn,'Register',se,'','FTSRegisterAgent')
    else:
      gLogger.info("FTSRegister.execute: No waiting registrations found.")
    return S_OK()
