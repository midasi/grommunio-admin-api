# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2020 grammm GmbH

import ldap3
import ldap3.core.exceptions as exc
from ldap3.utils.conv import escape_filter_chars

import logging
import yaml

from .config import Config
from .misc import GenericObject


class LDAPGuard:
    """LDAP connection proxy class."""

    def __init__(self, Base, *args, **kwargs):
        self.__base = Base
        self.__args = args
        self.__kwargs = kwargs
        self.error = None
        self.__obj = None
        self.__connect()

    def __getattr__(self, name):
        if not self.__connect():
            raise self.error
        attr = getattr(self.__obj, name)
        if callable(attr):
            def proxyfunc(*args, **kwargs):
                try:
                    return attr(*args, **kwargs)
                except (exc.LDAPSocketOpenError, exc.LDAPSocketSendError, exc.LDAPSessionTerminatedByServerError):
                    logging.warn("LDAP socket error - reconnecting")
                    if not self.__connect(True):
                        raise self.error
                    nattr = getattr(self.__obj, name)
                    return nattr(*args, **kwargs)
            return proxyfunc
        return attr

    def __connect(self, reconnect=False):
        if self.__obj is not None and not reconnect:
            return True
        try:
            self.__obj = self.__base(*self.__args, **self.__kwargs)
            return True
        except exc.LDAPBindError as err:
            logging.error("Could not connect to ldap server: "+err.args[0])
            self.error = err
        except exc.LDAPSocketOpenError as err:
            logging.error("Could not connect to ldap server: " + err.args[0])
            self.error = err
        return False

    def __repr__(self):
        return repr(self.__obj)


_defaultProps = {"storagequotalimit": Config["ldap"].get("users", {}).get("defaultQuota", 42)}

try:
    with open("res/ldapTemplates.yaml") as file:
        _templates = yaml.load(file, Loader=yaml.SafeLoader)
except:
    _templates = {}


def _flattenProps(props):
    return [{"name": key, "val": value} for key, value in props.items()]


def _matchFilters(ID):
    """Generate match filters string.

    Includes a filter for each entry in ldap.users.filters and adds ID filter.

    Parameters
    ----------
    ID : str or bytes
        Object ID of the LDAP person

    Returns
    -------
    str
        A string containing LDAP match filter expression.
    """
    ldapconf = Config["ldap"]
    filters = ")(".join(f for f in ldapconf.get("users", {}).get("filters", ()))
    return "(&({}={}){})".format(ldapconf["objectID"],
                                 escape_filter_chars(ID),
                                 ("("+filters+")") if len(filters) else "")


def _matchFiltersMulti(IDs):
    """Generate match filters string for multiple IDs.

    Includes a filter for each entry in ldap.users.filters and adds ID filters.

    Parameters
    ----------
    IDs : list of bytes or str
        List of IDs to match

    Returns
    -------
    str
        A string containing LDAP match filter expression.
    """
    ldapconf = Config["ldap"]
    filters = ")(".join(f for f in ldapconf.get("users", {}).get("filters", ()))
    IDfilters = "(|{})".format("".join("({}={})".format(ldapconf["objectID"], escape_filter_chars(ID)) for ID in IDs))
    return "(&({}){})".format(filters, IDfilters)


def _searchFilters(query, domains=None):
    """Generate search filters string.

    Includes a filter for each entry in ldap.users.filters and adds substring filters for all attributes in
    ldap.users.searchAttributes.
    Optionally, an additional list of permitted domains can be used to further restrict matches.

    Parameters
    ----------
    query : str
        Username to search for.

    Returns
    -------
    str
        A string including all search filters.
    """
    ldapconf = Config["ldap"]
    username = ldapconf["users"]["username"]
    filterexpr = "".join("("+f+")" for f in ldapconf["users"].get("filters", ()))
    domainexpr = "(|{})".format("".join("({}=*@{})".format(username, d) for d in domains)) if domains is not None else ""
    if query is not None:
        query = escape_filter_chars(query)
        searchexpr = "(|{})".format("".join(("("+sattr+"=*"+query+"*)" for sattr in ldapconf["users"]["searchAttributes"])))
    else:
        searchexpr = ""
    return "(&{}{}{})".format(filterexpr, searchexpr, domainexpr)


def _searchBase():
    """Generate directory name to search.

    If configured, adds the ldap.users.subtree path to ldap.baseDn. Otherwise only ldap.baseDn is returned.

    Returns
    -------
    str
        LDAP directory to search for users.
    """
    if "users" in ldapconf and "subtree" in ldapconf["users"]:
        return ldapconf["users"]["subtree"]+","+ldapconf["baseDn"]
    return ldapconf["baseDn"]


def authUser(ID, password):
    """Attempt ldap bind for user with given ID and password

    Parameters
    ----------
    ID : str or bytes
        ID of the LDAP object representing the user
    password : str
        User password.

    Returns
    -------
    str
        Error message if authentication failed or None if successful
    """
    if not LDAP_available:
        return "LDAP not configured"
    LDAPConn.search(_searchBase(), _matchFilters(ID))
    if len(LDAPConn.response) == 0:
        return "Invalid Username or password"
    if len(LDAPConn.response) > 1:
        return "Multiple entries found - please contact your administrator"
    userDN = LDAPConn.response[0]["dn"]
    try:
        ldap3.Connection(ldapconf["connection"].get("server"), user=userDN, password=password, auto_bind=True)
    except exc.LDAPBindError:
        return "Invalid username or Password"


def getUserInfo(ID):
    """Get e-mail address of an ldap user.

    Parameters
    ----------
    ID : str or bytes
        Object ID of the LDAP user
    Returns
    -------
    GenericObject
        Object containing LDAP ID, username and display name of the user
    """
    if not LDAP_available:
        return None
    users = ldapconf["users"]
    username, name = users["username"], users["displayName"]
    LDAPConn.search(_searchBase(), _matchFilters(ID), attributes=[username, name, ldapconf["objectID"]])
    if len(LDAPConn.response) != 1:
        return None
    return GenericObject(ID=LDAPConn.entries[0][ldapconf["objectID"]].raw_values[0],
                         username=LDAPConn.entries[0][username].value,
                         name=LDAPConn.entries[0][name].value,
                         email=LDAPConn.entries[0][username].value)


def getAll(IDs):
    """Get user information for each ID.

    Queries the same information as getUserInfo.

    Parameters
    ----------
    IDs : list of bytes or str
        IDs o search

    Returns
    -------
    list
        List of GenericObjects with information about found users
    """
    if not LDAP_available:
        return []
    users = ldapconf["users"]
    username, name= users["username"], users["displayName"]
    LDAPConn.search(_searchBase(), _matchFiltersMulti(IDs), attributes=[username, name, ldapconf["objectID"]])
    return [GenericObject(ID=entry[ldapconf["objectID"]].raw_values[0],
                          username=entry[username].value,
                          name=entry[name].value,
                          email=entry[username].value)
            for entry in LDAPConn.entries]


def downsyncUser(ID, props=None):
    """Create dictionary representation of the user from LDAP data.

    The dictionary can be used to create or update a orm.users.Users object.

    Parameters
    ----------
    ID : str
        LDAP ID of the user object
    props : dict, optional
        UserProperties as dictionary. The default is a dictionary containing storagequotalimit property.

    Raises
    ------
    RuntimeError
        LDAP query failed

    Returns
    -------
    userdata : dict
        Dictionary representation of the LDAP user
    """
    if not LDAP_available:
        return None
    LDAPConn.search(_searchBase(), _matchFilters(ID), attributes=["*", ldapconf["objectID"]])
    if len(LDAPConn.response) == 0:
        return None
    if len(LDAPConn.response) > 1:
        raise RuntimeError("Multiple entries found - aborting")
    ldapuser = LDAPConn.entries[0]
    userdata = dict(username=ldapuser[ldapconf["users"]["username"]].value)
    userdata["properties"] = props or _defaultProps.copy()
    userdata["properties"].update({prop: ldapuser[attr].value for attr, prop in _userAttributes.items() if attr in ldapuser})
    return userdata


def searchUsers(query, domains=None):
    """Search for ldap users matchig the query.

    Parameters
    ----------
    query : str
        String to match
    domains : list of str, optional
        Optional domain filter. The default is None.

    Returns
    -------
    list
        List of user objects containing ID, e-mail and name
    """
    if not LDAP_available:
        return []
    IDattr = ldapconf["objectID"]
    name, email = ldapconf["users"]["displayName"], ldapconf["users"]["username"]
    LDAPConn.search(_searchBase(),
                    _searchFilters(query, domains),
                    attributes=[IDattr, name, email],
                    paged_size=25)
    return [GenericObject(ID=result[IDattr].raw_values[0],
                          email=result[email].value,
                          name=result[name].value)
            for result in LDAPConn.entries]


def dumpUser(ID):
    """Download complete user description.

    Parameters
    ----------
    ID : str ot bytes
        LDAP object ID of the user

    Returns
    -------
    ldap3.abstract.entry.Entry
        LDAP object or None if not found or ambiguous
    """
    LDAPConn.search(_searchBase(), _matchFilters(ID), attributes=["*", ldapconf["objectID"]])
    return LDAPConn.entries[0] if len(LDAPConn.entries) == 1 else None


def _createConnection(*args, **kwargs):
    conn = ldap3.Connection(*args, **kwargs)
    if ldapconf["connection"].get("starttls", False):
        if not conn.start_tls():
            logging.error("Failed to initiate StartTLS LDAP connection")
    if not conn.bind():
        raise exc.LDAPBindError("LDAP bind failed ({}): {}".format(conn.result["description"], conn.result["message"]))
        return None
    return conn

def _checkConfig():
    ldapconf = Config["ldap"]
    return "baseDn" in ldapconf and\
           "objectID" in ldapconf and\
           "users" in ldapconf and\
           "username" in ldapconf["users"] and\
           "searchAttributes" in ldapconf["users"] and\
           "displayName" in ldapconf["users"]


if _checkConfig():
    ldapconf = Config["ldap"]
    try:
        LDAPConn = LDAPGuard(_createConnection,
                             ldapconf["connection"].get("server"),
                             user=ldapconf["connection"].get("bindUser"),
                             password=ldapconf["connection"].get("bindPass"))
        _templatesEnabled = ldapconf["users"].get("templates", [])
        _userAttributes = {}
        for _template in _templatesEnabled:
            _userAttributes.update(_templates.get(_template, {}))
        _userAttributes.update(ldapconf["users"].get("attributes", {}))
        LDAP_available = LDAPConn.error is None
    except BaseException as err:
        logging.error("LDAP initialization failed: "+err.args[0])
        LDAP_available = False
else:
    logging.warn("Incomplete LDAP configuration found - service disabled")
    LDAP_available = False
