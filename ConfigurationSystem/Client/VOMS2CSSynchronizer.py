""" VOMS2CSSyncronizer is a helper class containing the logic for synchronization
    of the VOMS user data with the DIRAC Registry
"""

from collections import defaultdict

from DIRAC import S_OK, S_ERROR, gLogger, gConfig

from DIRAC.Core.Security.VOMSService import VOMSService
from DIRAC.Core.Utilities.List import fromChar
from DIRAC.Core.Utilities.PrettyPrint import printTable
from DIRAC.ConfigurationSystem.Client.CSAPI import CSAPI
from DIRAC.ConfigurationSystem.Client.Helpers.Registry import getVOOption, getVOMSRoleGroupMapping, \
    getUsersInVO, getAllUsers

__RCSID__ = "$Id$"


class VOMS2CSSynchronizer(object):

  def __init__(self, vo, autoModifyUsers=True, autoAddUsers=True, autoDeleteUsers=False):

    self.log = gLogger.getSubLogger("VOMS2CSSynchronizer")
    self.csapi = CSAPI()
    self.vo = vo
    self.vomsVOName = getVOOption(vo, "VOMSName", "")
    if not self.vomsVOName:
      raise Exception("VOMS name not defined for VO %s" % vo)
    self.adminMsgs = {'Errors': [], 'Info': []}
    self.vomsUserDict = {}
    self.autoModifyUsers = autoModifyUsers
    self.autoAddUsers = autoAddUsers
    self.autoDeleteUsers = autoDeleteUsers

  def syncCSWithVOMS(self):
    """ Performs the synchronization of the DIRAC registry with the VOMS data. The resulting
        CSAPI object containing modifications is returned as part of the output dictionary.
        Those changes can be applied by the caller depending on the mode (dry or a real run)

    :return: S_OK with a dictionary containing the results of the synchronization operation
    """
    resultDict = defaultdict(list)

    # Get DIRAC group vs VOMS Role Mappings
    result = getVOMSRoleGroupMapping(self.vo)
    if not result['OK']:
      return result

    vomsDIRACMapping = result['Value']['VOMSDIRAC']
    diracVOMSMapping = result['Value']['DIRACVOMS']
    noVOMSGroups = result['Value']['NoVOMS']
    noSyncVOMSGroups = result['Value']['NoSyncVOMS']

    vomsSrv = VOMSService(self.vo)

    # Get VOMS users
    result = vomsSrv.getUsers()
    if not result['OK']:
      self.log.error('Could not retrieve user information from VOMS', result['Message'])
      return result

    self.vomsUserDict = result['Value']
    message = "There are %s user entries in VOMS for VO %s" % (len(self.vomsUserDict), self.vomsVOName)
    self.adminMsgs['Info'].append(message)
    self.log.info(message)

    # Get DIRAC users
    result = self.getVOUserData(self.vo)
    if not result['OK']:
      return result
    diracUserDict = result['Value']
    self.adminMsgs['Info'].append("There are %s registered users in DIRAC for VO %s" % (len(diracUserDict), self.vo))
    self.log.info("There are %s registered users in DIRAC VO %s" % (len(diracUserDict), self.vo))

    # Find new and obsoleted user DNs
    existingDNs = []
    obsoletedDNs = []
    newDNs = []
    for user in diracUserDict:
      dn = diracUserDict[user]['DN']
      # We can have users with more than one DN registered
      dnList = fromChar(dn)
      existingDNs.extend(dnList)
      for dn in dnList:
        if dn not in self.vomsUserDict:
          obsoletedDNs.append(dn)

    for dn in self.vomsUserDict:
      if dn not in existingDNs:
        newDNs.append(dn)

    allDiracUsers = getAllUsers()
    nonVOUserDict = {}
    nonVOUsers = list(set(allDiracUsers) - set(diracUserDict.keys()))
    if nonVOUsers:
      result = self.csapi.describeUsers(nonVOUsers)
      if not result['OK']:
        self.log.error('Could not retrieve CS User description')
        return result
      nonVOUserDict = result['Value']

    # Process users
    defaultVOGroup = getVOOption(self.vo, "DefaultGroup", "%s_user" % self.vo)
    newAddedUserDict = {}
    for dn in self.vomsUserDict:
      newDNForExistingUser = ''
      diracName = ''
      if dn in existingDNs:
        for user in diracUserDict:
          if dn == diracUserDict[user]['DN']:
            diracName = user

      if dn in newDNs:
        # Find if the DN is already registered in the DIRAC CS
        for user in nonVOUserDict:
          if dn == nonVOUserDict[user]['DN']:
            diracName = user

        # Check the nickName in the same VO to see if the user is already registered
        # with another DN
        nickName = self.vomsUserDict[dn].get('nickname')
        if nickName in diracUserDict or nickName in newAddedUserDict:
          diracName = nickName
          # This is a flag for adding the new DN to an already existing user
          newDNForExistingUser = dn

        # We have a real new user
        if not diracName:
          if nickName:
            newDiracName = nickName
          else:
            newDiracName = self.getUserName(dn)

          # Do not consider users with Suspended status in VOMS
          if self.vomsUserDict[dn]['suspended'] or self.vomsUserDict[dn]['certSuspended']:
            resultDict["SuspendedUsers"].append(newDiracName)
            continue

          # If the chosen user name exists already, append a distinguishing suffix
          ind = 1
          trialName = newDiracName
          while newDiracName in allDiracUsers:
            # We have a user with the same name but with a different DN
            newDiracName = "%s_%d" % (trialName, ind)
            ind += 1

          # We now have everything to add the new user
          userDict = {"DN": dn, "CA": self.vomsUserDict[dn]['CA'], "Email": self.vomsUserDict[dn]['mail']}
          groupsWithRole = []
          for role in self.vomsUserDict[dn]['Roles']:
            groupList = vomsDIRACMapping.get(role, [])
            for group in groupList:
              if group not in noSyncVOMSGroups:
                groupsWithRole.append(group)
          userDict['Groups'] = list(set(groupsWithRole + [defaultVOGroup]))
          message = "\n  Added new user %s:\n" % newDiracName
          for key in userDict:
            message += "    %s: %s\n" % (key, str(userDict[key]))
          self.adminMsgs['Info'].append(message)
          self.voChanged = True
          if self.autoAddUsers:
            self.log.info("Adding new user %s: %s" % (newDiracName, str(userDict)))
            result = self.csapi.modifyUser(newDiracName, userDict, createIfNonExistant=True)
            if not result['OK']:
              self.log.warn('Failed adding new user %s' % newDiracName)
            resultDict['NewUsers'].append(newDiracName)
            newAddedUserDict[newDiracName] = userDict
          continue

      # We have an already existing user
      modified = False
      userDict = {"DN": dn, "CA": self.vomsUserDict[dn]['CA'], "Email": self.vomsUserDict[dn]['mail']}
      if newDNForExistingUser:
        userDict['DN'] = ','.join([dn, diracUserDict.get(diracName, newAddedUserDict.get(diracName))['DN']])
        modified = True
      existingGroups = diracUserDict.get(diracName, {}).get('Groups', [])
      nonVOGroups = list(set(existingGroups) - set(diracVOMSMapping.keys()))
      groupsWithRole = []
      for role in self.vomsUserDict[dn]['Roles']:
        groupList = vomsDIRACMapping.get(role, [])
        for group in groupList:
          if group not in noSyncVOMSGroups:
            groupsWithRole.append(group)
      keepGroups = nonVOGroups + groupsWithRole + [defaultVOGroup]
      for group in existingGroups:
        if group in nonVOGroups:
          continue
        role = diracVOMSMapping.get(group, '')
        # Among already existing groups for the user keep those without a special VOMS Role
        # because this membership is done by hand in the CS
        if "Role" not in role:
          keepGroups.append(group)
        # Keep existing groups with no VOMS attribute if any
        if group in noVOMSGroups:
          keepGroups.append(group)
        # Keep groups for which syncronization with VOMS is forbidden
        if group in noSyncVOMSGroups:
          keepGroups.append(group)
      userDict['Groups'] = list(set(keepGroups))
      # Merge together groups for the same user but different DNs
      if diracName in newAddedUserDict:
        otherGroups = newAddedUserDict[diracName].get('Groups', [])
        userDict['Groups'] = list(set(keepGroups + otherGroups))
        modified = True

      # Check if something changed before asking CSAPI to modify
      if diracName in diracUserDict:
        message = "\n  Modified user %s:\n" % diracName
        modMsg = ''
        for key in userDict:
          if key == "Groups":
            addedGroups = set(userDict[key]) - set(diracUserDict.get(diracName, {}).get(key, []))
            removedGroups = set(diracUserDict.get(diracName, {}).get(key, [])) - set(userDict[key])
            if addedGroups:
              modMsg += "    Added to group(s) %s\n" % ','.join(addedGroups)
            if removedGroups:
              modMsg += "    Removed from group(s) %s\n" % ','.join(removedGroups)
          else:
            oldValue = str(diracUserDict.get(diracName, {}).get(key, ''))
            if str(userDict[key]) != oldValue:
              modMsg += "    %s: %s -> %s\n" % (key, oldValue, str(userDict[key]))
        if modMsg:
          self.adminMsgs['Info'].append(message + modMsg)
          modified = True

      if self.autoModifyUsers and modified:
        result = self.csapi.modifyUser(diracName, userDict)
        if result['OK'] and result['Value']:
          self.log.info("Modified user %s: %s" % (diracName, str(userDict)))
          self.voChanged = True
          resultDict['ModifiedUsers'].append(diracName)

    # Check if there are potentially obsoleted users
    oldUsers = set()
    for user in diracUserDict:
      dnSet = set(fromChar(diracUserDict[user]['DN']))
      if not dnSet.intersection(set(self.vomsUserDict.keys())) and user not in nonVOUserDict:
        for group in diracUserDict[user]['Groups']:
          if group not in noVOMSGroups:
            oldUsers.add(user)

    # Check for obsoleted DNs
    for user in diracUserDict:
      dnSet = set(fromChar(diracUserDict[user]['DN']))
      for dn in dnSet:
        if dn in obsoletedDNs and user not in oldUsers:
          self.log.verbose("Modified user %s: dropped DN %s" % (user, dn))
          if self.autoModifyUsers:
            userDict = diracUserDict[user]
            modDNSet = dnSet - set([dn])
            if modDNSet:
              userDict['DN'] = ','.join(modDNSet)
              result = self.csapi.modifyUser(user, userDict)
              if result['OK'] and result['Value']:
                self.log.info("Modified user %s: dropped DN %s" % (user, dn))
                self.adminMsgs['Info'].append("Modified user %s: dropped DN %s" % (user, dn))
                self.voChanged = True
                resultDict['ModifiedUsers'].append(diracName)
            else:
              oldUsers.add(user)

    if oldUsers:
      self.voChanged = True
      if self.autoDeleteUsers:
        self.log.info('The following users will be deleted: %s' % str(oldUsers))
        result = self.csapi.deleteUsers(oldUsers)
        if result['OK']:
          self.adminMsgs['Info'].append('The following users are deleted from CS:\n  %s\n' % str(oldUsers))
          resultDict['DeletedUsers'] = oldUsers
        else:
          self.adminMsgs['Errors'].append('Error in deleting users from CS:\n  %s' % str(oldUsers))
          self.log.error('Error while user deletion from CS', result)
      else:
        self.adminMsgs['Info'].append('The following users to be checked for deletion:\n  %s' % str(oldUsers))
        self.log.info('The following users to be checked for deletion: %s' % str(oldUsers))

    resultDict['CSAPI'] = self.csapi
    resultDict['AdminMessages'] = self.adminMsgs
    return S_OK(resultDict)

  def getVOUserData(self, refreshFlag=False):
    """ Get a report for users of a given VO

    :param bool refreshFlag: flag to indicate that the configuration must be refreshed
                             before looking up user data
    :return: S_OK/S_ERROR, Value = user description dictionary
    """
    if refreshFlag:
      gConfig.forceRefresh()

    # Get DIRAC users
    diracUsers = getUsersInVO(self.vo)
    if not diracUsers:
      return S_ERROR("No VO users found for %s" % self.vo)

    if refreshFlag:
      result = self.csapi.downloadCSData()
      if not result['OK']:
        return result
    result = self.csapi.describeUsers(diracUsers)
    if not result['OK']:
      self.log.error('Could not retrieve CS User description')
    return result

  def getVOUserReport(self):
    """ Get a report string with the current status of the DIRAC Registry for the
        Virtual Organization

    :return: S_OK with the report string as Value
    """

    result = self.getVOUserData(refreshFlag=True)
    if not result['OK']:
      return result

    userDict = result['Value']

    # Get DIRAC group vs VOMS Role Mappings
    result = getVOMSRoleGroupMapping(self.vo)
    if not result['OK']:
      return result

    diracVOMSMapping = result['Value']['DIRACVOMS']
    records = []
    groupDict = defaultdict(int)
    multiDNUsers = {}
    suspendedUsers = []
    for user in userDict:
      for group in userDict[user]['Groups']:
        groupDict[group] += 1
      dnList = fromChar(userDict[user]['DN'])
      if len(dnList) > 1:
        multiDNUsers[user] = dnList
      if userDict[user].get('Status', 'Active') == 'Suspended':
        suspendedUsers.append(user)

    for group in diracVOMSMapping:
      records.append((group, str(groupDict[group]), diracVOMSMapping.get(group, '')))

    fields = ['Group', 'Number of users', 'VOMS Role']
    output = printTable(fields, records, sortField='Group', printOut=False, numbering=False)

    if multiDNUsers:
      output += '\nUsers with multiple DNs:\n'
      for user in multiDNUsers:
        output += '  %s:\n' % user
        for dn in multiDNUsers[user]:
          output += '    %s\n' % dn

    if suspendedUsers:
      output += '\n%d suspended users:\n' % len(suspendedUsers)
      output += '  %s' % ','.join(suspendedUsers)

    return S_OK(output)

  def getUserName(self, dn):
    """ Utility to construct user name

    :param str dn: user DN
    :return str: user name
    """
    name = self.vomsUserDict[dn].get('name')
    surname = self.vomsUserDict[dn].get('surname')
    if name and surname:
      surnameName = self.__getUserNameFromSurname(name, surname)
      return surnameName

    mail = self.vomsUserDict[dn]['mail']

    dnName = self.__getUserNameFromDN(dn)
    mailName = self.__getUserNameFromMail(mail)

    # If robot, take the dn based name
    if dnName.startswith('robot'):
      return dnName

    # Is mailName reasonable ?
    if len(mailName) > 5 and mailName.isalpha():
      return mailName

    # dnName too long
    if len(dnName) >= 12:
      return dnName[:11]

    # May be the mail name is still more reasonable
    if len(dnName) < len(mailName) and mailName.isalpha():
      return mailName

    return dnName

  def __getUserNameFromMail(self, mail):
    """ Utility to construct a reasonable user name from the user mail address

    :param str mail: e-mail address
    :return str: user name
    """

    mailName = mail.split('@')[0].lower()
    if '.' in mailName:
      # Most likely the mail contains the full user name
      names = mailName.split('.')
      name = names[0][0] + names[-1].lower()
      return name

    return mailName

  def __getUserNameFromDN(self, dn):
    """ Utility to construct a reasonable user name from the user DN
    :param str dn: user DN
    :return str: user name
    """

    shortVO = self.vo
    if '.' in self.vo:
      vos = self.vo.split('.')
      if vos[0] == 'vo':
        vos = vos[1:]
      if len(vos[-1]) == 2 or vos[-1] == 'org':
        vos = vos[:1]
      shortVO = '.'.join(vos)

    # Weird case of just a name as DN !
    if '/' not in dn and 'CN=' not in dn:
      dn = 'CN=' + dn
    entries = dn.split('/')
    entries.reverse()
    for entry in entries:
      if entry:
        # Weird case of no field name !
        if '=' not in entry:
          key, value = "CN", entry
        else:
          key, value = entry.split('=')
        if key.upper() == 'CN':
          ind = value.find("(")
          # Strip of possible words in parenthesis in the name
          if ind != -1:
            value = value[:ind]
          names = value.split()
          if len(names) == 1:
            nname = names[0].lower()
            if '.' in nname:
              names = nname.split('.')
              nname = (names[0][0] + names[-1]).lower()
            if '-' in nname:
              names = nname.split('-')
              nname = (names[0][0] + names[-1]).lower()
            return nname
          else:
            robot = False
            if names[0].lower().startswith("robot"):
              names.pop(0)
              robot = True
            for name in list(names):
              if name[0].isdigit() or "@" in name:
                names.pop(names.index(name))
            if robot:
              nname = "robot-%s-%s" % (names[-1].lower(), shortVO)
            else:
              nname = (names[0][0] + names[-1]).lower()
              if '.' in nname:
                names = nname.split('.')
                nname = (names[0][0] + names[-1]).lower()
            return nname

  def __getUserNameFromSurname(self, name, surname):
    """ Construct a reasonable userName from the user name and surname

    :param str name: user name
    :param str surname: user surname
    :return str: constructed user name
    """
    names = name.split()
    initials = ""
    for nn in names:
      initials += nn[0]
    surnames = surname.split()
    result = initials + surnames[-1]
    if len(result) >= 12:
      return result[:11]
    result = result.lower()
    return result
