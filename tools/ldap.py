# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2020 grammm GmbH

import ldap3
import ldap3.core.exceptions as exc
import re
from ldap3.utils.conv import escape_filter_chars

import logging
import yaml

from . import mconf
from .misc import GenericObject


class LDAPGuard:
    """LDAP connection proxy class."""

    def __init__(self, servers, *args, **kwargs):
        self.__base = self.__createConnection
        self.__servers = servers.split()
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
                    logging.warn("LDAP connection error - reconnecting")
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
            pool = ldap3.ServerPool(self.__servers, "FIRST", active=1) if len(self.__servers) != 1 else self.__servers[0]
            self.__obj = self.__base(pool, *self.__args, **self.__kwargs)
            self.error = None
            return True
        except (exc.LDAPBindError, exc.LDAPSocketOpenError, exc.LDAPServerPoolExhaustedError) as err:
            self.error = err
        return False

    def __repr__(self):
        return repr(self.__obj)

    @staticmethod
    def __createConnection(server, bindUser, bindPass, starttls):
        conn = ldap3.Connection(server, user=bindUser, password=bindPass)
        if starttls:
            if not conn.start_tls():
                logging.error("Failed to initiate StartTLS LDAP connection")
        if not conn.bind():
            raise exc.LDAPBindError("LDAP bind failed ({}): {}".format(conn.result["description"], conn.result["message"]))
            return None
        return conn


_defaultProps = {}
_unescapeRe = re.compile(rb"\\(?P<value>[a-fA-F0-9]{2})")
_userAttributes = None
LDAPConn = None
LDAP_available = False
ldapconf = {}


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
    filters = ")(".join(f for f in ldapconf["users"].get("filters", ()))
    return "(&({}={}){}{})".format(ldapconf["objectID"],
                                 escape_filter_chars(ID),
                                 ("("+filters+")") if len(filters) else "",
                                 ldapconf["users"].get("filter", ""))


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
    filters = ldapconf["users"].get("filters", ())
    filters = ("("+")(".join(filters) + ")") if len(filters) > 0 else ""
    IDfilters = "(|{})".format("".join("({}={})".format(ldapconf["objectID"], escape_filter_chars(ID)) for ID in IDs))
    return "(&{}{}{})".format(filters, IDfilters, ldapconf["users"].get("filter", ""))


def _searchFilters(query, domains=None, userconf=None):
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
    conf = userconf or ldapconf["users"]
    username = conf["username"]
    filterexpr = "".join("("+f+")" for f in conf.get("filters", ()))
    domainexpr = "(|{})".format("".join("({}=*@{})".format(username, d) for d in domains)) if domains is not None else ""
    if query is not None:
        query = escape_filter_chars(query)
        searchexpr = "(|{})".format("".join(("("+sattr+"=*"+query+"*)" for sattr in conf["searchAttributes"])))
    else:
        searchexpr = ""
    return "(&{}{}{}{})".format(filterexpr, searchexpr, domainexpr, conf.get("filter", ""))


def _searchBase(conf=None):
    """Generate directory name to search.

    If configured, adds the ldap.users.subtree path to ldap.baseDn. Otherwise only ldap.baseDn is returned.

    Returns
    -------
    str
        LDAP directory to search for users.
    """
    conf = conf or ldapconf
    if "users" in conf and "subtree" in conf["users"]:
        return conf["users"]["subtree"]+","+conf["baseDn"]
    return conf["baseDn"]


def _userComplete(user, required=None):
    """Check if LDAP object provides all required fields.

    If no required fields are specified, the default `objectID`,
    `users.username` and `users.displayname` config values are used.

    Parameters
    ----------
    user : LDAP object
        Ldap object to check
    required : iterable, optional
        List of field names. The default is None.

    Returns
    -------
    bool
        True if all required fields are present, False otherwise

    """
    props = required or (ldapconf["users"]["username"], ldapconf["users"]["displayName"], ldapconf["objectID"])
    res = all(prop in user and user[prop].value is not None for prop in props)
    return res


def unescapeFilterChars(text):
    """Reverse escape_filter_chars function.

    In contrast to ldap3.utils.conv.unescape_filter_chars, this function also processes arbitrary byte escapes.

    Parameters
    ----------
    text : str
        String generated by ldap3.utils.conv.escape_filter_chars


    Returns
    -------
    bytes
        bytes object containing unescaped data
    """
    raw = bytes(text, "utf-8")
    last = 0
    unescaped = bytes()
    for match in _unescapeRe.finditer(raw):
        unescaped += raw[last:match.start()]+bytes((int(match.group("value"), 16),))
        last = match.end()
    return unescaped if last != 0 else raw


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
    try:
        LDAPConn.search(_searchBase(), _matchFilters(ID), attributes=[username, name, ldapconf["objectID"]])
    except exc.LDAPInvalidValueError:
        return None
    if len(LDAPConn.response) != 1 or not _userComplete(LDAPConn.entries[0]):
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
            for entry in LDAPConn.entries if _userComplete(entry)]


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
    try:
        LDAPConn.search(_searchBase(), _matchFilters(ID), attributes=["*", ldapconf["objectID"]])
    except:
        return None
    if len(LDAPConn.entries) == 0:
        return None
    if len(LDAPConn.entries) > 1:
        raise RuntimeError("Multiple entries found - aborting")
    ldapuser = LDAPConn.entries[0]
    if not _userComplete(ldapuser, (ldapconf["users"]["username"],)):
        return None
    userdata = dict(username=ldapuser[ldapconf["users"]["username"]].value)
    userdata["properties"] = props or _defaultProps.copy()
    userdata["properties"].update({prop: ldapuser[attr].value for attr, prop in _userAttributes.items() if attr in ldapuser})
    if ldapconf["users"].get("aliases"):
        aliasattr = ldapconf["users"]["aliases"]
        if aliasattr in ldapuser and ldapuser[aliasattr].value is not None:
            aliases = ldapuser[aliasattr].value
            userdata["aliases"] = aliases if isinstance(aliases, list) else [aliases]
        else:
            userdata["aliases"] = []
    return userdata


def searchUsers(query, domains=None, limit=25):
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
    try:
        exact = getUserInfo(unescapeFilterChars(query))
        exact = [] if exact is None or not _userComplete(exact) else [exact]
    except:
        exact = []
    LDAPConn.search(_searchBase(),
                    _searchFilters(query, domains),
                    attributes=[IDattr, name, email],
                    paged_size=limit)
    return exact+[GenericObject(ID=result[IDattr].raw_values[0],
                                email=result[email].value,
                                name=result[name].value)
                  for result in LDAPConn.entries if _userComplete(result)]


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


def _testConfig(ldapconf):
    for required in ("baseDn", "objectID", "users", "connection"):
        if required not in ldapconf or ldapconf[required] is None or len(ldapconf[required]) == 0:
            raise KeyError("Missing required config value '{}'".format(required))
    if "server" not in ldapconf["connection"] or ldapconf["connection"]["server"] is None or\
       len(ldapconf["connection"]["server"]) == 0:
        raise KeyError("Missing required config value 'connection.server'")
    for required in ("username", "searchAttributes", "displayName"):
        if required not in ldapconf["users"] or ldapconf["users"][required] is None or len(ldapconf["users"][required]) == 0:
            raise KeyError("Missing required config value 'users.{}'".format(required))
    _templatesEnabled = ldapconf["users"].get("templates", [])
    _userAttributes = {}
    for _template in _templatesEnabled:
        if _template not in _templates:
            raise ValueError("Unknown template '{}'".format(_template))
        _userAttributes.update(_templates.get(_template, {}))
    _userAttributes.update(ldapconf["users"].get("attributes", {}))
    if ldapconf.get("disabled", False):
        return None, _userAttributes
    LDAPConn = LDAPGuard(ldapconf["connection"].get("server"),
                         ldapconf["connection"].get("bindUser"),
                         ldapconf["connection"].get("bindPass"),
                         ldapconf["connection"].get("starttls", False))
    if LDAPConn.error is not None:
        raise LDAPConn.error
    if "filter" in ldapconf["users"]:
        f = ldapconf["users"]["filter"]
        if f is not None and len(f) != 0 and f[0] != "(" and f[-1] != ")":
            ldapconf["users"]["filter"] = "("+f+")"
    global _defaultProps
    if "defaultQuota" in ldapconf["users"]:
        _defaultProps = {prop: ldapconf["users"]["defaultQuota"] for prop in ("storagequotalimit", "prohibitsendquota", "prohibitreceivequota")}
    else:
        _defaultProps = {}
    LDAPConn.search(_searchBase(ldapconf), _searchFilters(" ", userconf=ldapconf["users"]), attributes=[], paged_size=0)
    return LDAPConn, _userAttributes


def reloadConfig(conf=None):
    """Reload LDAP configuration.

    Parameters
    ----------
    conf : dict
        New configuration

    Returns
    -------
    str
        Error message or None if successful
    """
    conf = conf or mconf.LDAP
    global LDAPConn, ldapconf, _userAttributes, LDAP_available
    try:
        LDAPConn, _userAttributes = _testConfig(conf)
        ldapconf = conf
        LDAP_available = LDAPConn is not None
        return
    except KeyError as err:
        return "Incomplete LDAP configuration: "+err.args[0]
    except ValueError as err:
        return "Invalid LDAP configuration: "+err.args[0]
    except Exception as err:
        return "Could not connect to LDAP server: "+" - ".join(str(v) for v in err.args)


def disable():
    """Disable LDAP service."""
    global LDAPConn, LDAP_available
    LDAPConn = None
    LDAP_available = False


def _init():
    err = reloadConfig(mconf.LDAP)
    if err is not None:
        logging.warn("Could not initialize LDAP: "+err+". Service plugin disabled.")

ldap3.set_config_parameter("POOLING_LOOP_TIMEOUT", 1)
_init()
