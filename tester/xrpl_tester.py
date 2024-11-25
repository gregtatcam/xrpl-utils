#!/usr/bin/env python3
#import gnureadline as readline
import readline
import os
import atexit
import requests
import sys
import random
import json
import time
import re
import pprint
import binascii
from collections import defaultdict
from xrpl.clients import JsonRpcClient
from xrpl.core.addresscodec import *
from xrpl.models import XRP, IssuedCurrencyAmount, Payment, RipplePathFind
from xrpl.transaction import autofill_and_sign
from xrpl.wallet import generate_faucet_wallet

do_pprint = True
drops_per_xrp = 1_000_000
port = 51234
node = '127.0.0.1'
fund = False
script = None
store = {}
tx_wait = 0.1
tx_wait_save = tx_wait
auto_accept = False
debug = False
i = 1
while i < len(sys.argv):
    if sys.argv[i] == '--node':
        i += 1
        node = sys.argv[i]
    elif sys.argv[i] == '--port':
        i += 1
        port = int(sys.argv[i])
    elif sys.argv[i] == '--script':
        i += 1
        script = sys.argv[i]
    elif sys.argv[i] == '--fund':
        fund = True
    elif sys.argv[i] == '--debug':
        debug = True
    else:
        raise Exception(f'invalid argument {sys.argv[i]}')
    i += 1

genesis_acct = 'rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh'
genesis_sec = 'snoPBrXtMeMyMHUVTgbuqAfg1SUTb'
burn_acct = None
burn_sec = None
mainnet = 'http:://s1.ripple.com'
ammdevnet = 'http://amm.devnet.rippletest.net'
devnet = 'http://s.devnet.rippletest.net'
testnet = 'http://s.altnet.rippletest.net'

# accounts keyed by alias, store account_id and master_seed as a pair
accounts = defaultdict(defaultdict)
# currency to the issuer map, can have more than 3 letters/digits, like USD1.
# this allows same currency but different issuer.
# only first three letters are used in the payload.
issuers = defaultdict()
# commands history
history = list()
# print each outgoing request if true
verbose = False
verbose_hash = False
# mpts to the mptid map, can have any number of characters
# can not be the same as existing issuers
mpts = defaultdict()
mpts_alias = defaultdict()

readline.set_history_length(1000)
# File to store the command history
HISTORY_FILE = os.path.expanduser(".history.json")

# Load history if it exists
if os.path.exists(HISTORY_FILE):
    readline.read_history_file(HISTORY_FILE)

# Save history on exit
atexit.register(readline.write_history_file, HISTORY_FILE)

validFlags = {'noDirectRipple':65536, 'partialPayment':131072, 'limitQuality':262144,
             'passive':65536, 'immediateOrCancel':131072, 'fillOrKill': 262144,
             'sell': 524288, 'accountTxnID':5, 'authorizedNFTokenMinter': 10, 'defaultRipple': 8,
             'depositAuth': 9, 'disableMaster': 4, 'disallowXRP': 3, 'globalFreeze': 7, 'noFreeze': 6,
             'requireAuth': 2, 'requireDest': 1, 'withdrawAll': 0x20, 'noRippleDirect': 65536,
              'LPToken': 0x00010000, 'WithdrawAll': 0x00020000, 'OneAssetWithdrawAll': 0x00040000,
              'SingleAsset': 0x000080000, 'TwoAsset': 0x00100000, 'OneAssetLPToken': 0x00200000,
              'LimitLPToken': 0x00400000, 'setNoRipple': 0x00020000, 'clearNoRipple': 0x00040000,
              'TwoAssetIfEmpty': 0x00800000,
              'MPTCanLock': 0x02, 'MPTRequireAuth': 0x04, 'MPTCanEscrow': 0x08, 'MPTCanTrade': 0x10,
              'MPTCanTransfer': 0x20, 'MPTCanClawback': 0x40, 'MPTUnauthorize': 0x01, 'MPTLock': 0x01,
              'MPTUnlock': 0x02}

def load_history():
    global history
    try:
        history = list()
        h_len = readline.get_current_history_length()
        for i in range(2, h_len + 1):
            history.append(readline.get_history_item(i))
    except Exception as e:
        print(e)
        history = list()

def load_accounts_():
    global accounts
    try:
        with open('accounts.json', 'r') as f:
            accounts = json.load(f)
    except:
        accounts = defaultdict(defaultdict)

def load_issuers():
    global issuers
    try:
        with open('issuers.json', 'r') as f:
            issuers = json.load(f)
    except:
        issuers = defaultdict()

def load_mpts():
    global mpts
    global mpts_alias
    try:
        with open('mpts.json', 'r') as f:
            mpts = json.load(f)
        with open('mpts_alias.json', 'r') as f:
            mpts_alias = json.load(f)
    except:
        mpts = defaultdict()
        mpts_alias = defaultdict()

def dump_history():
    global history
    readline.write_history_file(HISTORY_FILE)

def dump_accounts():
    global accounts
    with open('accounts.json', 'w') as f:
        json.dump(accounts, f)

def dump_issuers():
    global issuers
    with open('issuers.json', 'w') as f:
        json.dump(issuers, f)

def dump_mpts():
    global mpts
    with open('mpts.json', 'w') as f:
        json.dump(mpts, f)
    with open('mpts_alias.json', 'w') as f:
        json.dump(mpts_alias, f)

load_history()
load_accounts_()
load_issuers()
load_mpts()

def save_wait():
    global tx_wait
    global tx_wait_save
    tx_wait_save = tx_wait
    tx_wait = 0.1

def restore_wait(do_wait = True):
    global tx_wait
    global tx_wait_save
    tx_wait = tx_wait_save
    if do_wait:
        time.sleep(tx_wait)

class Re:
    def __init__(self):
        self.match = None
    def search(self, rx, s):
        self.match = re.search(rx, s)
        return self.match is not None

def get_store(v, totype: type = None):
    rx = Re()
    if rx.search(r'^\$([^\s]+)$', v):
        return store[rx.match[1]]
    if type is not None:
        return totype(v)
    return v

def error(res):
    if 'result' in res:
        if 'engine_result' in res['result']:
            if res['result']['engine_result'] == 'tesSUCCESS':
                return False
            else:
                print('error:', res['result']['engine_result_message'])
        elif 'status' in res['result']:
            if res['result']['status'] == 'success':
                return False
            else:
                print('error:', res['result']['status'])
        else:
            print('error:', res)
    else:
        print('error:', res)
    return True


def isAddress(account):
    rx = Re()
    return (account is not None and
            account.startswith('r') and
            len(account) >= 25 and
            len(account) <= 35 and
            rx.search(r'^\w+$', account) and
            not rx.search(r'[IlO0]', account))

def getAccountId(account):
    if account is not None:
        if isAddress(account):
            return account
        if account in accounts:
            return accounts[account]['id']
    return None

def getAlias(id):
    for alias,account in accounts.items():
        if account['id'] == id:
            return alias
    return None

#[LedgerEntryType:[MPToken;Offer],Account,MPTAmount,MPTokenIssuanceID]
def make_objects_filter(str):
    rx = Re()
    filter = None
    if rx.search(r'\[([^\s]+)\]', str):
        try:
            filter = {}
            fstr = rx.match[1]
            while fstr != '':
                # k:v or k:[v1,...] or k
                if rx.search(r'^(([^:,]+:[^:,\[]+)|([^:,]+:\[[^\]]+\])|([^:,\]\]]+))(,(.+))?$', fstr):
                    s = rx.match[1]
                    if rx.match[5] is not None:
                        fstr = rx.match[6]
                    else:
                        fstr = ''
                # also requesting to match a value
                if rx.search(r'([^\s]+):([^\s]+)', s):
                    k = rx.match[1]
                    v = rx.match[2]
                    # list of values
                    if rx.search(r'\[([^\s]+)\]', v):
                        filter[k] = {e for e in rx.match[1].split(',')}
                    else:
                        filter[k] = {v}
                else:
                    filter[s] = None
        except:
            filter = None
        finally:
            str = re.sub(r'\[.+\]', '', str)
    return (filter, str)


""" [],[]
"Paths" : [
      [
         {
            "currency" : "XRP",
            "issuer" : "rrrrrrrrrrrrrrrrrrrrrhoLvTp"
         },
         {
            "currency" : "USD",
            "issuer" : "r9QxhA9RghPZBbUchA9HkrmLKaWvkLXU29"
         }
      ]
   ],
"""
# [XRP,USD],[GBP,USD]
def getPaths(paths):
    global issuers
    pathsa = []
    if paths is None:
        return None
    rx = Re()
    for path in paths.split('],['):
        ps = []
        for cur in path.strip('[]').split(','):
            if cur == 'XRP':
                ps.append({"currency": cur, "issuer": "rrrrrrrrrrrrrrrrrrrrrhoLvTp"})
            elif cur in issuers:
                ps.append({"currency": getCurrency(cur), "issuer": getAccountId(issuers[cur])})
            elif cur in mpts:
                ps.append({"mpt_issuance_id": mpts[cur]})
            elif rx.search(r'^\$([^\s]+)$', cur):
                issue = getAMMIssue(rx.match[1])
                ps.append({"currency":issue.currency, "issuer":issue.issuer})
            else:
                return []
        pathsa.append(ps)
    return pathsa

# noDirectRipple,partialPayment,limitQuality
def getFlags(flags, default):
    if flags is None:
        return default
    rx = Re()
    if rx.search(r'^\s*([^\s]+)\s*$', flags):
        n = 0
        for flag in rx.match[1].split(','):
            if flag in validFlags:
                n |= validFlags[flag]
        if n == 0:
            raise Exception(f'invalid flags {flags}')
        return str(n)
    return default

def getCurrency(currency):
    return currency[0:3] if not currency.startswith('03') else currency

class Issue:
    def __init__(self, currency, issuer = None, mpt_id = None):
        if mpt_id is not None:
            self.mpt_id = mpt_id
            self.issuer = None
            self.currency = None
        else:
            self.mpt_id = None
            # hack to pass in drops
            self.drops = False
            if len(currency) == 3:
                currency = currency.upper()
            if currency == 'XRPD':
                currency = 'XRP'
                self.drops = True
            self.currency = currency
            if issuer is None and currency != 'XRP':
                self.issuer = getAccountId(issuers[currency])
            else:
                self.issuer = issuer
    def native(self):
        return self.currency is not None and self.currency == 'XRP'
    def is_mpt(self):
        return self.mpt_id is not None
    def isDrops(self):
        return self.drops
    # XRP|IOU|$AMM
    def nextFromStr(s, with_issuer = False):
        rx = Re()
        # AMM issue
        if rx.search(r'^\s*\$([^\s]+)(.*)', s):
            issue = getAMMIssue(rx.match[1])
            if issue is None:
                print(rx.match[1], 'issue not found')
                return (None, s)
            return (issue, rx.match[2])
        elif rx.search(r'^\s*([^\s]+)(.*)$', s):
            # check if mptid
            if rx.match[1] in mpts:
                return (Issue(None, None, mpts[rx.match[1]]), rx.match[2])
            else:
                currency = rx.match[1].upper()
                rest = rx.match[2]
                if currency == 'XRP' or currency == 'XRPD':
                    return (Issue(currency, None), rest)
                # it may have an issuer but doesn't have to
                # it could be next amount
                if with_issuer and rx.search(r'^\s+([^\s]+)(.*)$', rest):
                    # match[1] might be account
                    id = getAccountId(rx.match[1])
                    # matched the issuer
                    if id is not None:
                        return (Issue(currency, id), rx.match[2])
                if currency in issuers:
                    return (Issue(currency, getAccountId(issuers[currency])), rest)
        return (None, s)
    def fromStr(s, with_issuer = False):
        (iou, rest) = Issue.nextFromStr(s, with_issuer)
        if iou is not None and rest == '':
            return iou
        return None
    def __eq__(self, other):
        if self.currency is not None:
            return self.currency == other.currency and self.issuer == other.issuer
        return self.mpt_id == other.mpt_id
    def __ne__(self, other):
        return not (self == other)
    def toStr(self):
        if self.currency is not None:
            return f'{self.currency}/{self.issuer}'
        return self.mpt_id
    def json(self):
        if self.mpt_id is not None:
            return """
            {
                "mpt_issuance_id" : "%s"
            }
            """ % (self.mpt_id)
        elif self.native():
            return """
            {
                "currency" : "XRP"
            }
            """
        else:
            return """
            {
                "currency" : "%s",
                "issuer": "%s"
            }
            """ % (getCurrency(self.currency), self.issuer)
    def fromJson(j):
        if type(j) == str and j == 'XRP':
            return Issue('XRP')
        elif 'mpt_issuance_id' in j:
            return Issue(None, None, j['mpt_issuance_id'])
        elif j['currency'] == 'XRP':
            return Issue('XRP')
        return Issue(j['currency'], j['issuer'])
    def assetStr(self):
        if self.mpt_id is not None:
            if self.mpt_id in mpts_alias:
                return mpts_alias[self.mpt_id]
            return None
        return self.currency

class Amount:
    def __init__(self, issue: Issue, value : float):
        if issue.native() and not issue.isDrops():
            self.value = value * drops_per_xrp
        else:
            self.value = value
        self.issue = issue
    def json(self):
        if self.issue.native():
            return """ "%d" """ % self.value
        elif self.issue.is_mpt():
            return """
            {
                "mpt_issuance_id" : "%s",
                "value" : "%d"
            }
            """ % (self.issue.mpt_id, self.value)
        else:
            return """
            {
                "currency" : "%s",
                "issuer": "%s",
                "value": "%s"
            }
            """ % (getCurrency(self.issue.currency), self.issue.issuer, self.value)
    def fromIssue(iou: Issue):
        return Amount(iou, 0)
    # <amount XRP|IOU
    def nextFromStr(s, with_issuer = False):
        rx = Re()
        if not rx.search(r'^\s*([\-]?\d+(\.\d+)?)\s*(.+)$', s):
            return (None, s)
        amount = float(rx.match[1])
        rest = rx.match[3]
        iou, rest = Issue.nextFromStr(rest, with_issuer)
        if iou is not None:
            return (Amount(iou, amount), rest)
        return (None, s)
    def fromJson(j):
        if type(j) == str:
            return Amount(Issue('XRPD'), float(j))
        elif 'mpt_issuance_id' in j:
            return Amount(Issue(None, None, j['mpt_issuance_id']), float(j['value']))
        else:
            return Amount(Issue(j['currency'], j['issuer']), float(j['value']))
    def fromLineJson(j):
        if 'mpt_issuance_id' in j:
            return Amount(Issue(None, None, j['mpt_issuance_id']), float(j['balance']))
        return Amount(Issue(j['currency'], j['account']), float(j['balance']))
    def fromStr(s, with_issuer = False):
        (amt, rest) = Amount.nextFromStr(s, with_issuer)
        if amt is not None and rest == '':
            return amt
        return None
    def toStr(self):
        if self.issue.currency is not None:
            return f'{self.value}/{self.issue.currency}'
        return f'{self.value}/{self.issue.mpt_id}'
    def __eq__(self, other):
        return self.issue == other.issue and self.value == other.value
    def __ne__(self, other):
        return not (self == other)
    def __add__(self, other):
        if type(other) == Amount:
            if self.issue != other.issue:
                raise Exception('can not add, amounts have different Issue')
            return Amount(self.issue, self.value + other.value)
        if type(other) == int or type(other) == float:
            return Amount(self.issue, self.value + other)
        raise Exception('can not add amounts')
    def __sub__(self, other):
        if type(other) == Amount:
            if self.issue != other.issue:
                raise Exception('can not subtract, amounts have different Issue')
            return Amount(self.issue, self.value - other.value)
        if type(other) == int or type(other) == float:
            return Amount(self.issue, self.value - other)
        raise Exception('can not add amounts')
    def __mul__(self, other):
        if type(other) == Amount:
            if self.issue != other.issue:
                raise Exception('can not multiply, amounts have different Issue')
            return Amount(self.issue, self.value * other.value)
        if type(other) == int or type(other) == float:
            return Amount(self.issue, self.value * other)
        raise Exception('can not multiply amounts')

def fix_comma(s: str):
    return re.sub(r',\s*}', '\n}', s)

# convert some fields
def cvt_fields(json_obj):
    keys = {'MPTAmount', 'AssetPrice', 'MaximumAmount', 'OutstandingAmount', 'LockedAmount',
            'ExchangeRate', 'BaseFee'}
    if isinstance(json_obj, list):
        for a in json_obj:
            cvt_fields(a)
    elif isinstance(json_obj, dict):
        if 'DeliveredAmount' in json_obj and 'mpt_issuance_id' in json_obj['DeliveredAmount']:
            json_obj['DeliveredAmount']['value'] = int(json_obj['DeliveredAmount']['value'], 16)
        # delivered_amount is not hex?
        #elif 'delivered_amount' in json_obj and 'mpt_issuance_id' in json_obj['delivered_amount']:
        #    json_obj['delivered_amount']['value'] = int(json_obj['delivered_amount']['value'], 16)
        for key, value in json_obj.items():
            if isinstance(value, dict):
                cvt_fields(value)
            elif isinstance(value, list):
                for a in value:
                    cvt_fields(a)
            else:
                if key in keys:
                    json_obj[key] = int(value, 16)
                if key == 'mpt_issuance_id' and json_obj[key] in mpts_alias:
                    json_obj[key] = mpts_alias[json_obj[key]]
                if key == 'MPTokenIssuanceID' and json_obj[key] in mpts_alias:
                    json_obj[key] = mpts_alias[json_obj[key]]

def get_tx_hash(j):
    if 'result' in j and 'tx_json' in j['result'] and 'hash' in j['result']['tx_json']:
        return j['result']['tx_json']['hash']
    return None

def accept():
    ledger_accept('ledger accept')


def send_request(request, node = None, port = '51234'):
    if node == None:
        node = "1"
    if re.search(r'^\d+$', node):
        url = 'http://127.0.0.%s:%s' % (node, port)
    elif re.search(r'^http', node):
        url = f'{node}:{port}'
    else:
        url = 'http://%s:%s' % (node, port)
    if verbose:
        j = json.loads(request)
        if do_pprint:
            cvt_fields(j)
        print(do_format(pprint.pformat(j)))
    res = requests.post(url, json = json.loads(request))

    if res.status_code != 200:
        raise Exception(res.text)
    j = json.loads(request)
    if (verbose or verbose_hash) and 'method' in j and j['method'] == 'submit':
        if verbose_hash:
            j1 = json.loads(res.text)
            hash = get_tx_hash(j1)
            if hash is not None:
                print('Ok:200', hash)
        else:
            print(res.text)
    if 'method' in j and j['method'] == 'submit':
        if auto_accept:
            accept()
        else:
            time.sleep(tx_wait)
    j = json.loads(res.text)
    try:
        if do_pprint:
            cvt_fields(j)
    finally:
        pass
    return j

def quoted(val):
    if type(val) == str and val != 'true' and val != 'false':
        return f'"%s"' % val
    return str(val)

def get_field(field, val, delim=True, asis=False, num=False, rev_delim=False):
    d = ',' if delim and not rev_delim else ''
    ret = ''
    if val is None:
        ret = ""
    elif num and re.search(r'^\d+', val):
        ret = """
        "%s": %s%s
        """ % (field, val, d)
    elif asis:
        ret  = """
        "%s": %s%s
        """ % (field, val, d)
    elif type(val) == Amount or type(val) == Issue:
        ret = """
        "%s": %s%s
        """ % (field, val.json(), d)
    elif type(val) == str and re.search(r'false|true', val):
        ret = """
        "%s": %s%s
        """ % (field, val, d)
    else:
        ret = """
        "%s": %s%s
        """ % (field, json.JSONEncoder().encode(val), d)
    if rev_delim and ret != '':
        ret = ',' + ret
    return ret

def get_with_prefix(prefix, params):
    rx = Re()
    r = r'' + prefix + r'([^\s]+)'
    print(r)
    f = None
    if rx.search(r, params):
        f = rx.match[1]
        params = re.sub(r'\s*' + r, '', params)
    return f, params

def get_params(params, def_index = None):
    rx = Re()
    index = def_index
    hash = None
    if rx.search(r'#([^\s]+)', params):
        hash = rx.match[1]
        params = re.sub(r'\s*#[^\s]+', '', params)
    if rx.search(r'@([^\s]+)', params):
        index = rx.match[1]
        params = re.sub(r'\s*@[^\s]+', '', params)
    return (hash, index, params)

def get_params_ext(params):
    rx = Re()
    limit = None
    marker = None
    bool = None
    if rx.search(r'\^([^\s]+)', params):
        marker = rx.match[1]
        params = re.sub(r'\s*\^[^\s]+', '', params)
    if rx.search(r'\s*\$([^\s]+)', params):
        limit = int(rx.match[1])
        params = re.sub(r'\s*\$[^\s]+', '', params)
    if rx.search(r'(true|false)', params):
        bool = rx.match[1]
        params = re.sub(r'\s*(true|false)', '', params)
    return (limit, marker, bool, params)

def get_bool(params):
    rx = Re()
    bool = None
    if rx.search(r'(true|false)', params):
        bool = int(rx.match[1])
        params = re.sub(r'\s*(true|false)', '', params)
    return (bool, params)

def get_array(params):
    rx = Re()
    if rx.search(r'\[([^\s]+)\]', params):
        return rx.match[1].split(','), params
    return None, params


### Start requests
def faucet_send_request(request):
    if node == ammdevnet:
        res = requests.post('https://ammfaucet.devnet.rippletest.net/accounts', json=json.loads(request))
        if res.status_code != 200:
            raise Exception(res.text)
        return json.loads(res.text)
    else:
        client = JsonRpcClient('https://' + node + ':51234')
        # Creating wallet to send money from
        wallet = generate_faucet_wallet(client, debug=False)
        j = {}
        j['account'] = {}
        j['account']['address'] = wallet.address
        j['account']['secret'] = wallet.seed
        return j

def accountset_request(secret: str, account: str, t: str, flags: str, fee="10"):
    return """
        {
        "method": "submit",
        "params": [
            {
                "secret": "%s",
                "tx_json": {
                    "Account": "%s",
                    "TransactionType": "AccountSet",
                    "Fee": "%s",
                    "%s": "%s"
                }
            }
        ]
        }
        """ % (secret, account, fee, t, flags)

def account_delete_request(secret: str, account: str, destination: str, tag: str = None, fee="10", flags=0):
    return """
       {
        "method": "submit",
        "params": [
            {
                "secret": "%s",
                "tx_json": {
                    "TransactionType": "AccountDelete",
                    "Account": "%s",
                    "Destination": "%s",
                    "DestinationTag": 13,
                    "Fee": "%s",
                    "Flags": %d
                }
            }
        ]
        }
    """ % (secret, account, destination, get_field('DestinationTag', tag), fee, flags)

"""
{
   "Account" : "rH4KEcG9dEwGwpn6AyoWK9cZPLL4RLSmWW",
   "Amount" : {
      "currency" : "USD",
      "issuer" : "r9QxhA9RghPZBbUchA9HkrmLKaWvkLXU29",
      "value" : "100"
   },
   "Destination" : "rG1QQv2nh2gr7RCZ1P8YYcBUKCCN633jCn",
   "Fee" : "10",
   "Flags" : 131072,
   "Paths" : [
      [
         {
            "currency" : "USD",
            "issuer" : "r9QxhA9RghPZBbUchA9HkrmLKaWvkLXU29"
         }
      ]
   ],
   "SendMax" : {
      "currency" : "EUR",
      "issuer" : "r9QxhA9RghPZBbUchA9HkrmLKaWvkLXU29",
      "value" : "200"
   },
   "Sequence" : 6,
   "SigningPubKey" : "028949021029D5CC87E78BCF053AFEC0CAFD15108EC119EAAFEC466F5C095407BF",
   "TransactionType" : "Payment",
   "TxnSignature" : "3045022100E3593B5A660AE23567993F5DBFDD170FBD3EB457EF37A86FC7F022B28BA634DD0220721CCEE457C5C101B0FE2777F3154010C62771056B5837A95FE695CC58129786"
}
"""

def payment_request(secret, account, destination, amount: Amount,
                    paths: str = None,
                    sendMax: Amount = None,
                    fee = "10", flags = "2147483648"):
    paths = None if paths is None else json.dumps(paths)
    return fix_comma("""
    {
    "method": "submit",
    "params": [
        {
            "secret": "%s",
            "tx_json": {
                "Account": "%s",
                "Amount": %s,
                "Destination": "%s",
                "TransactionType": "Payment",
                "Fee": "%s",
                "Flags": "%s",
                %s
                %s
            }
        }
    ]
    }
    """ % (secret, account, amount.json(), destination, fee, flags,
           get_field('SendMax', sendMax), get_field('Paths', paths, asis=True)))

def wallet_request():
    return """
        {
           "method": "wallet_propose",
           "params": [{}]
        }
        """

def tx_request(hash, index = None, lhash = None):
    return """
    {
    "method": "tx",
    "params": [
        {
            "transaction": "%s",
            "binary": false
            %s
            %s
        }
    ]
    }
    """ % (hash, get_field('ledger_index', index, rev_delim=True), get_field('ledger_hash', lhash, rev_delim=True))

def tx_history_request(start=0):
    return """
    {
    "method": "tx_history",
    "params": [
        {
            "start": %d
        }
    ]
    }
    """ % (start)

def account_info_request(account, index='validated'):
    return """
    {
    "method": "account_info",
    "params": [
        {
            "account": "%s",
            "ledger_index": "%s"
        }
    ]
    }
    """ % (account, index)

# issuer - the address of the account to extend trust to
# value - limit of the trust
def trust_request(secret, account, amount: Amount, flags=262144, fee = '10'):
    return """
    {
    "method": "submit",
    "params": [
        {
            "secret": "%s",
            "tx_json": {
                "TransactionType": "TrustSet",
                "Account": "%s",
                "Fee": "%s",
                "Flags": %d,
                "LimitAmount": %s
            }
        }
    ]
    }
    """ % (secret, account, fee, flags, amount.json())

def account_trust_lines_request(account, index='validated'):
    return """
    {
    "method": "account_lines",
    "params": [
        {
            "account": "%s",
            "ledger_index": "%s"
        }
    ]
    }
    """ % (account, index)

def amm_create_request(secret, account, asset1: Amount, asset2: Amount, tradingFee="1", fee="10"):
    return """
   {
   "method": "submit",
   "params": [
       {
            "secret": "%s",
            "tx_json": {
                "Flags": 0,
                "Account" : "%s",
                "Fee": "%s",
                "TradingFee" : "%s",
                "Amount" : %s,
                "Amount2" : %s,
                "TransactionType" : "AMMCreate"
            }
       }
   ]
   }
   """ % (secret, account, fee, tradingFee, asset1.json(), asset2.json())

def amm_info_request(account, iou1: Issue = None, iou2: Issue = None, amm_account: str = None, index = "validated"):
   assert (iou1 and iou1) or (amm_account)
   return fix_comma(""" {
   "method": "amm_info",
   "params": [
       {
           %s
           %s
           %s
           %s
           %s
       }
   ]
   }
   """ % (get_field('account', account), get_field('asset', iou1), get_field('asset2', iou2),
          get_field('amm_account', amm_account), get_field('ledger_index', index)))

'''
LPTokenOut
Amount
Amount and Amount2
Amount and LPToken
Amount and EPrice
'''
def amm_deposit_request(secret, account, issues, tokens: Amount = None,
                        asset1: Amount = None, asset2: Amount = None,
                        eprice: Amount = None, fee="10", empty=False, tfee=1):
    flags = 0
    def fields():
        nonlocal flags
        if asset1 is not None:
            if asset2 is not None:
                flags = validFlags['TwoAssetIfEmpty'] if empty else validFlags['TwoAsset']
                return """
                    "Amount": %s,
                    "Amount2": %s,
                    %s
                """ % (asset1.json(), asset2.json(), get_field('TradingFee', tfee, False))
            if tokens is not None:
                flags = validFlags['OneAssetLPToken']
                return """
                "Amount": %s,
                "LPTokenOut": %s,
            """ % (asset1.json(), tokens.json())
            elif eprice is not None:
                flags = validFlags['LimitLPToken']
                return """
                "Amount": %s,
                "EPrice": %s,
                """ % (asset1.json(), eprice.json())
            else:
                flags = validFlags['SingleAsset']
                return f'"Amount": {asset1.json()},'
        elif tokens is not None:
            flags = validFlags['LPToken']
            return f'"LPTokenOut": {tokens.json()},'
    return """
    {
    "method": "submit",
    "params": [
        {
             "secret": "%s",
             "tx_json": {
                 "Account" : "%s",
                 "Asset" : %s,
                 "Asset2" : %s,
                 "Fee": "%s",
                 %s
                 "TransactionType" : "AMMDeposit",
                 "Flags": %d
             }
        }
    ]
    }
    """ % (secret, account, issues[0].json(), issues[1].json(), fee, fields(), flags)

'''
LPTokenIn
Amount
Amount and Amount2
Amount and LPToken
Amount and EPrice
'''
def amm_withdraw_request(secret, account, issues, tokens: Amount = None, asset1: Amount = None, asset2: Amount = None, eprice: Amount=None, fee="10"):
    flags = 0
    def fields():
        nonlocal flags
        if asset1 is not None:
            if asset2 is not None:
                flags = validFlags['TwoAsset']
                return """
                    "Amount": %s,
                    "Amount2": %s,
                """ % (asset1.json(), asset2.json())
            if tokens is not None:
                flags = validFlags['OneAssetLPToken']
                if tokens.value == 0.0:
                    flags = validFlags['OneAssetWithdrawAll']
                    return """
                        "Amount": %s,
                        """ % (asset1.json())
                return """
                    "Amount": %s,
                    "LPTokenIn": %s,
                    """ % (asset1.json(), tokens.json())
            if eprice is not None:
                flags = validFlags['LimitLPToken']
                return """
                "Amount": %s,
                "EPrice": %s,
                """ % (asset1.json(), eprice.json())
            else:
                flags = validFlags['SingleAsset']
                return f'"Amount": {asset1.json()},'
        elif tokens is not None:
            flags = validFlags['LPToken']
            if tokens.value == 0.0:
                flags = validFlags['WithdrawAll']
                return ""
            return f'"LPTokenIn": {tokens.json()},'
    return """
    {
    "method": "submit",
    "params": [
        {
             "secret": "%s",
             "tx_json": {
                 "Account" : "%s",
                 "Asset" : %s,
                 "Asset2" : %s,
                 "Fee": "%s",
                 %s
                 "TransactionType" : "AMMWithdraw",
                 "Flags": %d
             }
        }
    ]
    }
    """ % (secret, account, issues[0].json(), issues[1].json(), fee, fields(), flags)


def offer_request(secret, account, takerPays: Amount, takerGets: Amount, flags=0, fee="10"):
    return """
    {
    "method": "submit",
    "params": [
        {
            "secret": "%s",
            "tx_json": {
                "TransactionType": "OfferCreate",
                "Account": "%s",
                "Fee": "%s",
                "Flags": %d,
                "TakerPays": %s,
                "TakerGets": %s
            }
        }
    ]
    }
    """ % (secret, account, fee, flags, takerPays.json(), takerGets.json())

def offer_cancel_request(secret, account, seq, flags=0, fee="10"):
    return """
    {
    "method": "submit",
    "params": [
        {
            "secret": "%s",
            "tx_json": {
                "TransactionType": "OfferCancel",
                "Account": "%s",
                "Fee": "%s",
                "Flags": %d,
                "OfferSequence": %d
            }
        }
    ]
    }
    """ % (secret, account, fee, flags, seq)

def account_offers_request(account, hash = None, index = None):
    return """
    {
    "method": "account_offers",
    "params": [
        {
            %s
            %s
            "account": "%s"
        }
    ]
    }
    """ % (get_field('ledger_hash', hash), get_field('ledger_index', index), account)

def account_channels_request(account, destination = None, hash = None, index = None):
    return """
    {
    "method": "account_channels",
    "params": [
        {
            %s
            %s
            %s
            "account": "%s"
        }
    ]
    }
    """ % (get_field('destination_account', destination), get_field('ledger_hash', hash),
           get_field('ledger_index', index), account)


def account_currencies_request(account, strict = None, hash = None, index = None):
    return """
    {
    "method": "account_currencies",
    "params": [
        {
            %s
            %s
            %s
            "account": "%s"
        }
    ]
    }
    """ % (get_field('strict', strict), get_field('ledger_hash', hash), get_field('ledger_index', index),
           account)


def account_nfts_request(account, hash = None, index = None):
    return """
    {
    "method": "account_nfts",
    "params": [
        {
            %s
            %s
            "account": "%s",
        }
    ]
    }
    """ % (get_field('ledger_hash', hash), get_field('ledger_index', index), account)


def account_tx_request(account, hash=None, index=None, limit=None, binary=None,
                       marker=None, min=None, max=None, forward=None):
    return """
    {
    "method": "account_tx",
    "params": [{
            "account": "%s"
            %s
            %s
            %s
            %s
            %s
            %s
            %s
            %s
        }]
    }
    """ % (account, get_field('ledger_hash', hash, delim=False, rev_delim=True),
           get_field('ledger_index', index, delim=False, rev_delim=True),
           get_field('limit', limit, delim=False, rev_delim=True),
           get_field('binary', binary, delim=False, rev_delim=True),
           get_field('marker', marker, delim=False, rev_delim=True),
           get_field('ledger_index_min', min, delim=False, rev_delim=True),
           get_field('ledger_index_max', max, delim=False, rev_delim=True),
           get_field('forward', forward, False))

def gateway_balances_request(account, hash=None, index=None, strict=None, hotwallet=None):
    return """
    {
    "method": "gateway_balances",
    "params": [
        {
            %s
            %s
            %s
            %s
            "account": "%s"
        }
    ]
    }
    """ % (get_field('hotwallet', hotwallet), get_field('ledger_index', index),
           get_field('ledger_hash', hash), get_field('strict', strict), account)

def book_offers_request(taker_pays: Issue, taker_gets: Issue, limit = 10):
    return """
    {
    "method": "book_offers",
    "params": [
        {
            "taker": "r9cZA1mLK5R5Am25ArfXFmqgNwjZgnfk59",
            "taker_pays": %s,
            "taker_gets": %s,
            "limit": %d
        }
    ]
    }
    """ % (taker_pays.json(), taker_gets.json(), limit)

def vote_request(secret: str, account: str, issues,
                 feeVal: int, flags=0, fee="10"):
    return """
    {
    "method": "submit",
    "params": [
        {
            "secret": "%s",
            "tx_json": {
                "TransactionType": "AMMVote",
                "Account": "%s",
                "Asset": %s,
                "Asset2": %s,
                "TradingFee": %s,
                "Fee": "%s",
                "Flags": %d
            }
        }
    ]
    }
    """ % (secret, account, issues[0].json(), issues[1].json(), feeVal, fee, flags)

def bid_request(secret: str, account: str, issues,
                 pricet: str, bid: Amount, authAccounts = None, flags=0, fee="10"):
    if pricet == 'min':
        pricet = 'BidMin'
    else:
        pricet = 'BidMax'
    def get_bid():
        if bid.value != 0:
            return """
                "%s": %s,
            """ % (pricet, bid.json())
        return ""
    def get_accounts():
        if authAccounts is None:
            return ""
        s = '"AuthAccounts": ['
        d = ''
        for account in authAccounts:
            account_id = getAccountId(account)
            if account_id is None:
                raise Exception(f'Invalid account {account}')
            s += d+ '{"AuthAccount": {"Account": "' + account_id + '"}}'
            d = ','
        s += '],'
        return s

    return """
    {
    "method": "submit",
    "params": [
        {
            "secret": "%s",
            "tx_json": {
                "TransactionType": "AMMBid",
                "Account": "%s",
                "Asset": %s,
                "Asset2": %s,
                %s
                %s
                "Fee": "%s",
                "Flags": %d
            }
        }
    ]
    }
    """ % (secret, account, issues[0].json(), issues[1].json(), get_bid(), get_accounts(), fee, flags)

'''
def tx_request(txid):
    return """
    {
    "method": "tx",
    "params": [
        {
            "transaction": "%s",
            "binary": false
        }
    ]
    }
    """ % txid
'''

def ledger_entry_request(asset=None, asset2=None, id=None, index='validated', hash = None):
    assets_res = True if asset is not None and asset2 is not None else False
    id_res = True if id is not None else False
    assert (assets_res and not id_res) or (id_res and not assets_res)
    if id is not None:
        return """
        {
          "method": "ledger_entry",
          "params": [
            {
            "amm": "%s"
            %s
            %s
            }
          ]
        }
        """ % (id,
               get_field('ledger_index', index, rev_delim=True),
               get_field('ledger_hash', hash, rev_delim=True))
    else:
        return """
        {
          "method": "ledger_entry",
          "params": [
            {
            "amm": {
              "asset": %s,
              "asset2": %s
            },
            %s
            %s
            }
          ]
        }
        """ % (asset.json(), asset2.json(),
               get_field('ledger_index', index, rev_delim=True),
               get_field('ledger_hash', hash, rev_delim=True))


def ledger_entry_oracle_request(account, id, index = None, hash = None):
    return """
    {
      "method": "ledger_entry",
      "params": [
        {
        "oracle": {
        "account": "%s",
        "oracle_document_id": %d
        }
        %s
        %s
        }
      ]
    }
    """ % (account, int(id),
           get_field('ledger_index', index, num=True, rev_delim=True),
           get_field('ledger_hash', hash, rev_delim=True))

def ledger_entry_mpt_request(mpt_id, account = None, index = None, hash = None):
    if account is None:
        return """
        {
          "method": "ledger_entry",
          "params": [
            {
            "mpt_issuance": "%s"
            %s
            %s
            }
          ]
        }
        """ % (mpt_id,
               get_field('ledger_index', index, num=True, rev_delim=True),
               get_field('ledger_hash', hash, rev_delim=True))
    return """
            {
              "method": "ledger_entry",
              "params": [
                {
                "mptoken": {
                    "mpt_issuance": "%s",
                    "account": "%s"
                }
                %s
                %s
                }
              ]
            }
            """ % (mpt_id, account,
                   get_field('ledger_index', index, num=True, rev_delim=True),
                   get_field('ledger_hash', hash, rev_delim=True))

def ledger_data_request(hash=None, index=None, binary='false', limit='5', marker='None', type_=None):
    if hash is None and index is None:
        index = 'validated'
    if binary is None:
        binary = 'false'
    return fix_comma("""
    {
    "method": "ledger_data",
    "params": [
        {
            %s
            %s
            %s
            %s
            %s
            %s
        }
    ]
    }
    """ % (get_field('binary', binary), get_field('ledger_hash', hash), get_field('ledger_index', index),
           get_field('marker', marker), get_field('limit', limit), get_field('type', type_)))

def account_objects_request(account, hash=None, index=None, limit='5', marker='None', type_=None, delete_only = 'false'):
    if delete_only is None:
        delete_only = 'false'
    if limit is None:
        limit = 5
    return """
    {
    "method": "account_objects",
    "params": [
        {
            "account": "%s",
            %s
            %s
            %s
            "deletion_blokers_only": %s,
            "limit": %d
        }
    ]
    }
    """ % (account, get_field('ledger_index', index),
           get_field('type', type_), get_field('marker', marker), delete_only, limit)

def noripple_check_request(account, role, transactions, limit, hash, index):
    return """
    {
    "method": "noripple_check",
    "params": [
        {
            %s
            %s
            %s
            %s
            "role": "%s",
            "account": "r9cZA1mLK5R5Am25ArfXFmqgNwjZgnfk59"
        }
    ]
    }
    """ % (account, role, get_field('transactions', transactions), get_field('limit', limit),
           get_field('ledger_hash', hash), get_field('ledger_index', index))


def path_find_request(src: str, dst: str, dst_amount: Amount, send_max: Amount = None, src_curr: list = None):
    def get_curr(src_curr):
        if src_curr is None:
            return None
        l = []
        for curr in src_curr:
            if curr in mpts:
                l.append("mpt_issuance_id", mpts[curr])
            else:
                l.append({"currency": curr});
        return l

    return """
    {
    "method": "ripple_path_find",
    "params": [
        {
            "source_account" : "%s",
            "destination_account": "%s",
            %s
            %s
            %s
        }
    ]
    }
    """ % (src, dst,
           get_field('destination_amount', dst_amount, send_max is not None or src_curr is not None),
           get_field('send_max', send_max, src_curr is not None),
           get_field('source_currencies', get_curr(src_curr), False))

def oracle_set_request(secret, account, id, data_series):
    def make_data_series(data_series):
        delim = ''
        str = "["
        for data in data_series:
            scale = data[3] if len(data) == 4 and re.search(r'0', data[3]) is None else None
            price = data[2] if len(data) >= 3 else None
            str += delim + """
                {
                "PriceData" : {
                    "BaseAsset" : "%s",
                    "QuoteAsset" : "%s"
                    %s
                    %s
                }
                }
            """ % (data[0], data[1],
                   get_field('AssetPrice', price, rev_delim=True),
                   get_field('Scale', scale, rev_delim=True))
            delim = ','
        str += "]"
        return str
    return """
    {
    "method": "submit",
    "params": [
        {
             "secret": "%s",
             "tx_json": {
                 "Account" : "%s",
                 "AssetClass": "63757272656E6379",
                 "Fee" : "10",
                 "LastUpdateTime" : %d,
                 "OracleDocumentID" : %d,
                 "PriceDataSeries" : %s,
                "Provider" : "70726F7669646572",
                "TransactionType" : "OracleSet",
                "URI" : "555249"
             }
        }
    ]
    }
    """ % (secret, account, int(time.time()), int(id), make_data_series(data_series))

def oracle_delete_request(secret, account, id):
    return """
        {
        "method": "submit",
        "params": [
            {
                 "secret": "%s",
                 "tx_json": {
                     "Account" : "%s",
                     "OracleDocumentID" : %d,
                     "TransactionType" : "OracleDelete"
                 }
            }
        ]
        }
        """ % (secret, account, int(id))

def get_aggregate_price_request(base_asset, quote_asset, oracles):
    def make_oracles(oracles):
        delim = ''
        str = "["
        for oracle in oracles:
            account = oracle[0]
            id = oracle[1]
            str += delim + """
                {
                    "account" : "%s",
                    "oracle_document_id" : %s
                }
            """ % (account, id)
            delim = ','
        str += "]"
        return str
    return """
    {
        "method": "get_aggregate_price",
        "params": [
            {
                "base_asset": "%s",
                "quote_asset": "%s",
                "oracles": %s
            }
        ]
    }""" % (base_asset, quote_asset, make_oracles(oracles))

def get_ledger_request(hash = None, index = None):
    return """
        {
        "method": "ledger",
        "params": [
        {
            %s
            %s
        }
        ]
        }
        """ % (get_field('ledger_hash', hash, delim=False),
               get_field('ledger_index', index, delim=False))

def get_mpt_create_request(secret, account, maxAmt=None,
                           scale=None, tfee=None, meta=None, flags=None):
    return """
            {
            "method": "submit",
            "params": [
                {
                     "secret": "%s",
                     "tx_json": {
                         "Account" : "%s",
                         "TransactionType" : "MPTokenIssuanceCreate"
                         %s
                         %s
                         %s
                         %s
                         %s
                     }
                }
            ]
            }
            """ % (secret, account, get_field('MaximumAmount', maxAmt, num=True, rev_delim=True),
                   get_field('AssetScale', scale, num=True, rev_delim=True),
                   get_field('TransferFee', tfee, num=True, rev_delim=True),
                   get_field('MPTokenMetadata', meta, rev_delim=True),
                   get_field('Flags', str(flags), num=True, rev_delim=True))

def get_mpt_auth_request(secret, account, mpt_id, holder=None, flags=None):
    return """
            {
            "method": "submit",
            "params": [
                {
                     "secret": "%s",
                     "tx_json": {
                         "Account" : "%s",
                         "TransactionType" : "MPTokenAuthorize",
                         "MPTokenIssuanceID" : "%s"
                         %s
                         %s
                     }
                }
            ]
            }
            """ % (secret, account, mpt_id,
                   get_field('MPTokenHolder', holder, rev_delim=True),
                   get_field('Flags', str(flags), num=True, rev_delim=True))

def get_mpt_set_request(secret, account, mpt_id, holder=None, flags=None):
    return """
            {
            "method": "submit",
            "params": [
                {
                     "secret": "%s",
                     "tx_json": {
                         "Account" : "%s",
                         "TransactionType" : "MPTokenIssuanceSet",
                         "MPTokenIssuanceID" : "%s"
                         %s
                         %s
                     }
                }
            ]
            }
            """ % (secret, account, mpt_id,
                   get_field('MPTokenHolder', holder, rev_delim=True),
                   get_field('Flags', str(flags), num=True, rev_delim=True))

def get_mpt_destroy_request(secret, account, mpt_id):
    return """
            {
            "method": "submit",
            "params": [
                {
                     "secret": "%s",
                     "tx_json": {
                         "Account" : "%s",
                         "TransactionType" : "MPTokenIssuanceDestroy",
                         "MPTokenIssuanceID" : "%s"
                     }
                }
            ]
            }
            """ % (secret, account, mpt_id)


### End requests


def do_format(s):
    if not do_pprint:
        return s
    s = re.sub(genesis_acct, 'genesis', s)
    for (acct, d) in accounts.items():
        s = re.sub(d['id'], acct, s)
        if 'issue' in d:
            s = re.sub(d['issue']['currency'], acct, s)
    return s

def pair(s1, s2):
    j = {s1: s2}
    return do_format(pprint.pformat(j))

def faucet_fund1(name, req_XRP: int):
    global burn_acct
    global burn_sec
    j = faucet_send_request('{}')
    src_acct = j['account']['address']
    src_secret = j['account']['secret']
    xrp = 10000
    while xrp < req_XRP:
        faucet_send_request('{"destination":"%s"}' % src_acct)
        xrp += 10000
        time.sleep(0.1)
    if name is not None:
        set = accountset_request(src_secret, src_acct, 'SetFlag', "8")
        res = send_request(set, node, port)
        error(res)
        accounts[name] = {'id': src_acct, 'seed': src_secret}
    return (src_acct, src_secret)

# fund account[,account1,...] N XRP: fund via ammdevnet faucet
def faucet_fund(line):
    rx = Re()
    global accounts
    if rx.search(r'^\s*fund\s+([^\s]+)\s+(\d+)\s*XRP\s*$', line):
        names = rx.match[1]
        amount = int(rx.match[2])
        save_wait()
        try:
            for name in names.split(','):
                faucet_fund1(name, amount)
        except Exception as ex:
            raise ex
        finally:
            restore_wait()
        dump_accounts()
        return True
    return False

# fund account[,account1,...] <XRP>: call wallet_create and pay from genesis XRP into account,account1,...
def fund(line):
    if node == ammdevnet or ('http://' + node) == devnet or ('http://' + node) == testnet:
        return faucet_fund(line)
    rx = Re()
    global accounts
    if rx.search(r'^\s*fund\s+([^\s]+)\s+(\d+)\s*XRP\s*$', line):
        names = rx.match[1]
        amount = int(rx.match[2])
        save_wait()
        try:
            for name in names.split(','):
                res = send_request(wallet_request(), node, port)
                id = res['result']['account_id']
                seed = res['result']['master_seed']
                accounts[name] = {'id': id, 'seed': seed}
                payment = payment_request(genesis_sec,
                                          genesis_acct,
                                          id,
                                          Amount(Issue('XRP', None), amount),
                                          flags='0')
                res = send_request(payment, node, port)
                if error(res):
                    return True
                set = accountset_request(seed, id, 'SetFlag', "8")
                res = send_request(set, node, port)
                error(res)
        except Exception as ex:
            raise ex
        finally:
            restore_wait()
        dump_accounts()
        return True
    return False

# trust set account,account1,.. amount currency issuer [flags]
def trust_set(line):
    rx = Re()
    global accounts
    if rx.search(r'^\s*trust\s+set\s+([^\s]+)\s+(.+)$', line):
        accts = rx.match[1]
        rest = rx.match[2]
        (amount, rest) = Amount.nextFromStr(rest, True)
        if amount is None:
            print('invalid amount')
            return None
        flags = 262144 # clear no ripple
        if rest != '':
            rest = rest.strip(' ')
            if not rest in validFlags:
                print('invalid flags')
                return None
            flags = validFlags[rest]
        save_wait()
        try:
            for account in accts.split(','):
                if not account in accounts:
                    print(account, 'account not found')
                else:
                    request = trust_request(accounts[account]['seed'],
                                            accounts[account]['id'],
                                            amount,
                                            flags)
                    res = send_request(request, node, port)
                    if not error(res):
                        issuers[amount.issue.currency] = getAlias(amount.issue.issuer)
        except Exception as ex:
            raise ex
        finally:
            restore_wait()
        dump_issuers()
        return True
    return False

# account info account [$ledger] [\[balance,flags,..\]]
def account_info(line):
    rx = Re()
    global accounts
    if rx.search(r'^\s*account\s+info\s+([^\s]+)(\s+\$([^\s]+))?(\s+\[([^\s]+)\])?\s*$', line):
        for account in rx.match[1].split(','):
            id = getAccountId(account)
            if id is not None:
                index = 'validated'
                if rx.match[3] != None:
                    index = rx.match[3]
                request = account_info_request(id, index)
                res = send_request(request, node, port)
                if 'account_data' in res['result']:
                    if rx.match[5] is not None:
                        for field in rx.match[5].split(','):
                            if field in res['result']['account_data']:
                                print(pair(field, res['result']['account_data'][field]))
                    else:
                        print(do_format(pprint.pformat(res['result']['account_data'])))
                elif 'error_message' in res['result']:
                    print(do_format(pprint.pformat(res['result']['error_message'])))
                else:
                    print(do_format(pprint.pformat(res['result'])))
            else:
                print(account, 'account not found')
        return True
    return False

# account lines account [ledger] [\[cur1,cur2,..\]]
def account_lines(line):
    global accounts
    rx = Re()
    if rx.search(r'^\s*account\s+lines\s+([^\s]+)(\s+\$([^\s]+))?(\s+\[([^\s+]+)\])?\s*$', line):
        for account in rx.match[1].split(','):
            id = getAccountId(account)
            if id is not None:
                index = 'validated'
                if rx.match[3] != None:
                    index = rx.match[3]
                request = account_trust_lines_request(id, index)
                res = send_request(request, node, port)
                if rx.match[5] is not None:
                    for cur in rx.match[5].split(','):
                        iss = Issue.fromStr(cur)
                        cur_ = cur
                        if iss is not None:
                            cur_ = iss.currency
                        for l in res['result']['lines']:
                            if l['currency'] == cur_:
                                j = {'account': l['account'],
                                     'balance': l['balance'],
                                     'currency': l['currency'],
                                     'limit': l['limit'],
                                     }
                                print(do_format(pprint.pformat(j)))
                else:
                    print(do_format(pprint.pformat(res['result'])))
            else:
                print(rx.match[1], 'account not found')
        return True
    return False

# pay src dst[,dst1,...] amount currency [[path1,path2...] sendmax]
# path is [currency,...,currencyX]
# pay carol bob 100USD [[XRP,USD]] 120XRP
# path must be included for cross-currency, path could be empty if default; i.e. []
# pay carol bob 100USD [] 120XRP
def pay(line):
    global accounts
    rx = Re()
    if rx.search(r'^\s*pay\s+([^\s]+)\s+([^\s]+)\s+(.+)$', line):
        def checkDst(dsts):
            for dst in dsts.split(','):
                if dst not in accounts:
                    return dst
            return None

        src = rx.match[1]
        if src not in accounts:
            print(src, 'account not found')
            return None
        dsts = rx.match[2]
        check = checkDst(dsts)
        if check is not None:
            print(check, 'account not found')
            return None
        amt, rest = Amount.nextFromStr(rx.match[3])
        if amt is None:
            print('invalid amount')
            return None
        else:
            saveto = None
            if rx.search(r'save\s+to\s+\$([^\s]+)', rest):
                saveto = rx.match[1]
            paths = None
            sendmax = None
            flags = "2147483648"
            if rx.search(r'^\s*(\[[^\s]*\])\s+([^\s]+)(.*)?$', rest):
                paths = None if rx.match[1] == '[]' else getPaths(rx.match[1])
                if paths == []:
                    print('invalid paths')
                    return None
                sendmax = Amount.fromStr(rx.match[2])
                if sendmax is None:
                    print('invalid sendmax')
                    return None
                flags = getFlags(rx.match[3], flags)
            save_wait()
            try:
                for dst in dsts.split(','):
                    payment = payment_request(accounts[src]['seed'],
                                              accounts[src]['id'],
                                              accounts[dst]['id'],
                                              amt,
                                              paths=paths,
                                              sendMax=sendmax,
                                              flags=flags)
                    res = send_request(payment, node, port)
                    if error(res):
                        return True
                    if saveto is not None:
                        hash = get_tx_hash(res)
                        if hash is not None:
                            store[saveto] = hash
                    #if verbose:
                    #    print(res)
            except Exception as ex:
                raise ex
            finally:
                restore_wait()
        return True
    return False

# amm create [@alias] account currency currency [trading fee]
def amm_create(line):
    global accounts
    global verbose
    rx = Re()
    alias = None
    # amm alias is defined
    if rx.search(r'^\s*amm\s+create\s+@([^\s+]+)\s+', line):
        alias = rx.match[1];
        line = re.sub(r'@[^\s]+\s+', '', line)
    # account currency currency [trading fee]
    # currency may follow by the issuer: USD gw
    if rx.search(r'^\s*amm\s+create\s+([^\s]+)\s+(.+)$', line):
        account = rx.match[1]
        rest = rx.match[2]
        if not getAccountId(account):
            print(account, 'account not found')
            return None
        (amt1, rest) = Amount.nextFromStr(rest, True)
        if amt1 is None:
            print('invalid amount')
            return None
        (amt2, rest) = Amount.nextFromStr(rest, True)
        if amt2 is None:
            print('invalid amount')
            return None
        tfee = "0"
        if rx.search(r'^\s*(\d+)\s*$', rest):
            tfee = rx.match[1]
        if account not in accounts:
            print(account, 'account not found')
        else:
            res = send_request('{"method":"server_state"}', node, port)
            fee = res['result']['state']['validated_ledger']['reserve_inc']
            verboseSave = verbose
            request = amm_create_request(accounts[account]['seed'],
                                         accounts[account]['id'],
                                         amt1,
                                         amt2,
                                         tfee,
                                         fee)
            res = send_request(request, node, port)
            error(res)
            if alias is None:
                alias = f'amm{amt1.issue.assetStr()}-{amt2.issue.assetStr()}'
            else:
                # rm previous alias if exists
                alias_prev = f'amm{amt1.issue.assetStr()}-{amt2.issue.assetStr()}'
                if alias_prev in accounts:
                    del accounts[alias_prev]
            cur = f'{amt1.issue.assetStr()}-{amt2.issue.assetStr()}'
            verbose = False
            # force to close
            if not auto_accept:
                accept()
                time.sleep(1) # still need to wait for ledger to close
            request = amm_info_request(None, amt1.issue, amt2.issue, index='validated')
            res = send_request(request, node, port)
            verbose = verboseSave
            if 'result' not in res or 'amm' not in res['result']:
                print('amm create failed', res)
            else:
                result = res['result']['amm']
                ammAccount = result['account']
                tokens = result['lp_token']
                accounts[alias] = {'id': ammAccount,
                                   'token1': amt1.issue.json(),
                                   'token2': amt2.issue.json(),
                                   'issue': {'currency': tokens['currency'], 'issuer': tokens['issuer']}}
                dump_accounts()
        return True
    return False

def not_currency(c):
    return c != 'XRP' and c not in issuers and c not in mpts

# s is either a hash or amm alias
def getAMMHash(s):
    if s in accounts:
        return accounts[s]['hash']
    return s

def getAMMIssues(s):
    if s in accounts:
        return (Issue.fromJson(json.loads(accounts[s]['token1'])), Issue.fromJson(json.loads(accounts[s]['token2'])))
    return None

def getAMMIssue(s) -> Issue :
    if s in accounts:
        issue = accounts[s]['issue']
        return Issue(issue['currency'], issue['issuer'])
    for a,v in accounts.items():
        if 'hash' in v and v['hash'] == s:
            issue = v['issue']
            return Issue(issue['currency'], issue['issuer'])
    return None

# can pass either the asset pair or the amm account
# amm info [currency1 currency2] [amm_account] [account] [\[Amount,Amount2...\]] [@ledger] [save key]
def amm_info(line):
    global accounts
    def do_save(res, alias):
        result = res['result']
        tokens = result['lp_token']
        accounts[alias] = {'id': result['account'],
                           'issue': {'currency': tokens['currency'], 'issuer': tokens['issuer']}}
        dump_accounts()
    rx = Re()
    # save?
    save = None
    if rx.search(r'\s+save\s+([^\s]+)\s*$', line):
        save = rx.match[1]
        line = re.sub(r'\s+save\s+([^\s]+)\s*$', '', line)
    index = 'validated'
    if rx.search(r'\s+\@([^\s]+)', line):
        index = rx.match[1]
        line = re.sub(r'\s+\@([^\s]+)', '', line)
    if rx.search(r'^\s*amm\s+info\s+(.+)$', line):
        rest = rx.match[1]
        fields = None
        # match array of the amm fields
        if rx.search(r'\s+\[([^\s+]+)\]', rest):
            fields = rx.match[1].split(',')
            rest = re.sub(r'\s+\[([^\s+]+)\]', '', rest)
        # match amm account
        if rx.search(r'^\s*([^\s]+)(\s+([^\s]+))?\s*$', rest) and not_currency(rx.match[1]):
            amm_account = getAccountId(rx.match[1])
            if amm_account is None:
                print(rx.match[1], 'not found')
                return None
            account = rx.match[3]
            request = amm_info_request(getAccountId(account), amm_account=amm_account, index=index)
            res = send_request(request, node, port)
            if fields is not None:
                for field in fields:
                    if field in res['result']['amm']:
                        print(pair(field, res['result']['amm'][field]))
            else:
                print(do_format(pprint.pformat(res['result'])))
            return True
        (iou1, rest) = Issue.nextFromStr(rest)
        if iou1 is None:
            print('invalid issue')
            return None
        (iou2, rest) = Issue.nextFromStr(rest)
        if iou2 is None:
            print('invalid issue')
            return None
        account = None
        if rx.search(r'^\s*([^\s]+)\s*$', rest):
            account = rx.match[1]
        if account is not None and account not in accounts:
            print(account, 'account not found')
        else:
            request = amm_info_request(getAccountId(account),
                                       iou1,
                                       iou2,
                                       index=index)
            res = send_request(request, node, port)
            if fields is not None:
                for field in fields:
                    if field in res['result']['amm']:
                        print(pair(field, res['result']['amm'][field]))
            else:
                print(do_format(pprint.pformat(res['result'])))
            if save is not None:
                do_save(res, save)
        return True
    return False

# note: hash is an alias for issue1,issue2
# amm deposit account hash tokens
# amm deposit account hash asset1in
# amm deposit account hash asset1in asset2in [empty [tfee]]
# amm deposit account hash asset1in tokens
# amm deposit account hash asset1in @eprice: Note '@' to distinguish eprice from asset2in
def amm_deposit(line):
    rx = Re()
    if rx.search(r'^\s*amm\s+deposit\s+([^\s]+)\s+([^\s]+)\s+(.+)$', line):
        account = rx.match[1]
        account_id = getAccountId(rx.match[1])
        if account_id is None:
            print(rx.match[1], 'account not found')
            return None
        (issues) = getAMMIssues(rx.match[2])
        if issues is None:
            print(rx.match[2], 'tokens not found')
            return None
        issue = getAMMIssue(rx.match[2])
        rest = rx.match[3]
        tokens = None
        asset1 = None
        asset2 = None
        eprice = None
        empty = False
        tfee = None
        # tokens
        if rx.search(r'^\s*(\d+)\s*$', rest):
            tokens = Amount(issue, float(rx.match[1]))
        else:
            # issue tokens
            issue1, rest = Issue.nextFromStr(rest)
            if issue1 != None:
                asset1 = Amount(issue1, 0)
            # amount
            else:
                asset1, rest = Amount.nextFromStr(rest)
                if asset1 is None:
                    return False
            if rest != '':
                # tokens
                if rx.search(r'^\s*(\d+)\s*$', rest):
                    tokens = Amount(issue, float(rx.match[1]))
                # eprice
                elif rx.search(r'^\s*@([^\s]+)\s*$', rest):
                    eprice, rest = Amount.nextFromStr(rx.match[1])
                    if eprice is None or rest != '':
                        return False
                # amount
                else:
                    asset2, rest = Amount.nextFromStr(rest)
                    if rx.search(r'^\s*empty(\s+(\d+))?\s*$', rest):
                        empty = True
                        tfee = rx.match[2]
                        rest = ''
                    if asset2 is None or rest != '':
                        return False
        request = amm_deposit_request(accounts[account]['seed'], account_id, issues, tokens, asset1, asset2,
                                      eprice, empty=empty, tfee=tfee)
        res = send_request(request, node, port)
        error(res)
        return True
    return False

# amm withdraw account hash tokens
# amm withdraw account hash asset1in
# amm withdraw account hash asset1in asset2in
# amm withdraw account hash asset1in tokens
# amm withdraw account hash asset1in @eprice
def amm_withdraw(line):
    rx = Re()
    # account hash rest
    if rx.search(r'^\s*amm\s+withdraw\s+([^\s]+)\s+([^\s]+)\s+(.+)$', line):
        account = rx.match[1]
        account_id = getAccountId(rx.match[1])
        if account_id is None:
            print(rx.match[1], 'account not found')
            return None
        issues = getAMMIssues(rx.match[2])
        if issues is None:
            print(rx.match[2], 'tokens not found')
            return None
        issue = getAMMIssue(rx.match[2])
        rest = rx.match[3]
        tokens = None
        asset1 = None
        asset2 = None
        eprice = None
        # tokens
        if rx.search(r'^\s*(\d+)\s*$', rest):
            tokens = Amount(issue, float(rx.match[1]))
        else:
            # issue tokens
            issue1, rest = Issue.nextFromStr(rest)
            if issue1 != None:
                asset1 = Amount(issue1, 0)
            # amount
            else:
                asset1, rest = Amount.nextFromStr(rest)
                if asset1 is None:
                    return False
            if rest != '':
                # tokens
                if rx.search(r'^\s*(\d+)\s*$', rest):
                    tokens = Amount(issue, float(rx.match[1]))
                # eprice
                elif rx.search(r'^\s*@(\d+(\.\d+)?)\s*$', rest):
                    eprice = Amount(issue, float(rx.match[1]))
                # amount
                else:
                    asset2, rest = Amount.nextFromStr(rest)
                    if asset2 is None or rest != '':
                        return False
        request = amm_withdraw_request(accounts[account]['seed'], account_id, issues, tokens, asset1, asset2, eprice)
        res = send_request(request, node, port)
        error(res)
        return True
    return False


# offer create acct takerPaysAmt [gw] takerGetsAmt [gw]
def offer_create(line):
    rx = Re()
    if rx.search(r'^\s*offer\s+create\s+([^\s]+)\s+(.+)$', line):
        account = rx.match[1]
        id = getAccountId(account)
        if id is None:
            print(rx.match[1], 'account not found')
            return None
        rest = rx.match[2]
        takerPays, rest = Amount.nextFromStr(rest)
        if takerPays is None:
            # try with issuer
            takerPays, rest = Amount.nextFromStr(rest, True)
            if takerPays is None:
                return False
        takerGets,rest = Amount.nextFromStr(rest)
        if takerGets is None:
            # try with issuer
            takerGets, rest = Amount.nextFromStr(rest, True)
            if takerGets is None:
                return False
        flags = 0
        if rx.search(r'^\s*([^\s]+)\s*$', rest):
            flags = getFlags(rx.match[1], None)
            if flags is None:
                print('invalid flags')
                return None
        request = offer_request(accounts[account]['seed'], id, takerPays, takerGets, flags=int(flags))
        res = send_request(request, node, port)
        error(res)
        return True
    return False

def offer_cancel(line):
    def cancel(secret, id, seq):
        request = offer_cancel_request(secret, id, seq)
        res = send_request(request, node, port)
        error(res)

    rx = Re()
    if rx.search(r'^\s*offer\s+cancel\s+([^\s]+)(\s+([^\s]+))?\s*$', line):
        account = rx.match[1]
        id = getAccountId(account)
        if id is None:
            print(rx.match[1], 'account not found')
            return None
        if rx.match[3] is not None:
            for seq in rx.match[2].split(','):
                cancel(accounts[account]['seed'], id, int(seq))
        else:
            request = account_offers_request(id)
            res = send_request(request, node, port)
            for offer in res['result']['offers']:
                cancel(accounts[account]['seed'], id, offer['seq'])
        return True
    return False

# account offers account #hash @index
def account_offers(line):
    rx = Re()
    if rx.search(r'^\s*account\s+offers\s+([^\s]+)(.*)$', line):
        for account in rx.match[1].split(','):
            id = getAccountId(account)
            if id is None:
                print(account, 'account not found')
                return None
            hash, index, rest = get_params(rx.match[2])
            if rest != '':
                print('invalid command', rest)
                return None
            request = account_offers_request(id, hash, index)
            res = send_request(request, node, port)
            print(do_format(pprint.pformat(res['result'])))
        return True
    return False

# restore accounts created in the previous session
def session_restore(line):
    global accounts
    global issuers
    global history
    rx = Re()
    if rx.search(r'\s*session\s+restore\s*$', line):
        try:
            load_accounts_()
            load_issuers()
            for h in reversed(history):
                if rx.search(r'^\s*set\s+node', h):
                    exec_command(h)
                    return True
        except:
            pass
        return True
    return False

def do_history(line):
    rx = Re()
    if rx.search(r'^\s*h(istory)?(\s+\d+(-\d+)?)?\s*$', line):
        if rx.match[2] is None:
            for (i, h) in enumerate(history):
                print(i, h)
        else:
            h = rx.match[2].split('-');
            if len(h) == 1 and abs(int(h[0])) < len(history):
                start = int(h[0])
                end = start if start >= 0 else (start + 1)
            elif len(h) == 2 and (int(h[1]) < len(history) and int(h[0]) < int(h[1])):
                start = int(h[0])
                end = int(h[1])
            else:
                return False
            for i in range(start, end+1):
                print(history[i])
                exec_command(history[i])
    return True

def clear_history(line):
    global history
    rx = Re()
    if rx.search(r'^\s*clear\s+history\s*$', line):
        history = list()
        dump_history()
        return True
    return False

def clear_all(line):
    global accounts
    global issuers
    global mpts
    rx = Re()
    if rx.search(r'^\s*clear\s+all\s*$', line):
        accounts = defaultdict(defaultdict)
        issuers = defaultdict()
        mpts = defaultdict()
        mpts_alias = defaultdict()
        dump_accounts()
        dump_issuers()
        dump_mpts()
        return True
    return False


def show_accounts(line):
    global accounts
    rx = Re()
    if rx.search(r'^\s*accounts(\s+([^\s]+))?\s*$', line):
        do_match = rx.match[2]
        for k,v in accounts.items():
            if (do_match is not None and (((rx.search(rf'{do_match}', k) or rx.search(rf'{do_match}', str(v)))) and
                    rx.search(r'\-', k))) or do_match is None:
                print(k, v['id'])
        return True
    return False

def print_account(line):
    rx = Re()
    if rx.search(r'^\s*([^\s]+)\s*$', line) and rx.match[1] in accounts:
        print(accounts[rx.match[1]]['id'])
        return True
    return False

def show_issuers(line):
    rx = Re()
    global issuers
    if rx.search(r'^\s*issuers\s*$', line):
        for k,v in issuers.items():
            print(k,v)
        for k,v in mpts.items():
            print(k,v)
        return True
    return False

def toggle_verbose(line):
    rx = Re()
    global verbose
    global verbose_hash
    if rx.search(r'^\s*verbose\s+(on|off)(\s+all\s*)?$', line):
        on = rx.match[1] == 'on'
        verbose = on
        if rx.match[2] is not None:
            verbose_hash = on
        return True
    if rx.search(r'^\s*verbose\s+hash\s+(on|off)\s*$', line):
        verbose_hash = (rx.match[1] == 'on')
        return True
    return False

def set_node(line):
    rx = Re()
    global node
    global port
    if rx.search(r'^\s*set\s+node\s+(http://[^\s]+)(:(\d+))?\s*$', line):
        node = rx.match[1]
        port = 51234 if rx.match[3] is None else int(rx.match[3])
        return True
    elif rx.search(r'^\s*set\s+node\s+([^\s:]+)(:(\d+))?\s*$', line):
        node = rx.match[1]
        if node == 'ammdevnet':
            node = ammdevnet
        elif node == 'mainnet':
            node = 's1.ripple.com'
        elif node == 'devnet':
            node = 's.devnet.rippletest.net'
        elif node == 'testnet':
            node = 's.altnet.rippletest.net'
        port = 51234 if rx.match[3] is None else int(rx.match[3])
        return True
    return False

def set_account(line):
    global accounts
    rx = Re()
    if rx.search(r'^\s*set\s+account\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)\s*$', line):
        name = rx.match[1]
        id = rx.match[2]
        seed = rx.match[3]
        accounts[name] = {'id': id, 'seed': seed}
        with open('accounts.json', 'w') as f:
            json.dump(accounts, f)
        return True
    return False

def account_set(line):
    global accounts
    rx = Re()
    if rx.search(r'^\s*account\s+((SetFlag|ClearFlag)\s+)?([^\s]+)(\s+([^\s]+))?\s*$', line):
        t = rx.match[2]
        if t is None:
            t = 'SetFlag'
        for account in rx.match[3].split(','):
            id = getAccountId(account)
            if id is None:
                print(account, 'account not found')
                return None
            request = accountset_request(accounts[account]['seed'], id, t, flags=getFlags(rx.match[5], "8"))
            res = send_request(request, node, port)
            error(res)
        return True
    return False

def account_delete(line):
    global accounts
    rx = Re()
    if rx.search(r'^\s*account\s+delete\s+([^\s]+)\s+([^\s]+)(\s+(\d+))?\s*$', line):
        account = getAccountId(rx.match[1])
        if account is None:
            print(rx.match[1], 'account not found')
            return None
        destination = getAccountId(rx.match[2])
        if destination is None:
            print(destination, 'destination not found')
            return None
        tag = rx.match[5]
        request = account_delete_request(accounts[account]['seed'], account, destination, tag)
        res = send_request(request, node, port)
        error(res)
        accounts.pop(account)
        dump_accounts()
        return True
    return False

# account channels account #hash @index
def account_channels(line):
    rx = Re()
    if rx.search(r'^\s*account\s+channels\s+([^\s]+)(.*)$', line):
        account = getAccountId(rx.match[1])
        if account is None:
            print(rx.match[1], 'account not found')
            return None
        rest = rx.match[2]
        destination = None
        hash, index, rest = get_params(rest)
        if rest is not None:
            rest = rest.sub(r'\s+', '', rest)
            destination = getAccountId(rest)
            if destination is None:
                print(destination, 'destination not found')
                return None
        request = account_channels_request(account, destination, hash, index)
        res = send_request(request, node, port)
        print(do_format(pprint.pformat(res['result'])))
        return True
    return False


# account currencies account strict #hash @index
def account_currencies(line):
    rx = Re()
    if rx.search(r'^\s*account\s+currencies\s+([^\s]+)(.*)$', line):
        account = getAccountId(rx.match[1])
        if account is None:
            print(rx.match[1], 'account not found')
            return None
        rest = rx.match[2]
        hash, index, rest = get_params(rest)
        strict, rest = get_bool(rest)
        request = account_currencies_request(account, strict, hash, index)
        res = send_request(request, node, port)
        print(do_format(pprint.pformat(res['result'])))
        return True
    return False


# account nfts account #hash @index
def account_nfts(line):
    rx = Re()
    if rx.search(r'^\s*account\s+nfts\s+([^\s]+)(.*)$', line):
        account = getAccountId(rx.match[1])
        if account is None:
            print(rx.match[1], 'account not found')
            return None
        hash, index = get_params(rx.match[2])
        request = account_nfts_request(account, hash, index)
        res = send_request(request, node, port)
        print(do_format(pprint.pformat(res['result'])))
        return True
    return False

# ledger account [#hash] [@index] [$limit] [%binary] [^marker] [min-] [max-] [frwd-]
def account_tx(line):
    rx = Re()
    if rx.search(r'^\s*account\s+tx\s+([^\s]+)(.*)$', line):
        account = getAccountId(rx.match[1])
        rest = rx.match[2]
        if account is None:
            print(rx.match[1], 'account not found')
            return None
        forward = None
        min = None
        max = None
        hash, index, line = get_params(line)
        limit, marker, binary, line = get_params_ext(line)
        if rx.search(r'^min-([^\s]+)', line):
            min = rx.match[1]
        if rx.search(r'^max-([^\s]+)', line):
            max = rx.match[1]
        if rx.search(r'^frwd-([^\s]+)', line):
            forward = rx.match[1]
        request = account_tx_request(account, hash, index, limit, binary, marker, min, max, forward)
        res = send_request(request, node, port)
        print(do_format(pprint.pformat(res['result'])))
        return True
    return False


# gateway balances account strict [account,account] #hash @index
def gateway_balances(line):
    rx = Re()
    if rx.search(r'^\s*gateway\s+balances\s+([^\s]+)(.*)$', line):
        account = getAccountId(rx.match[1])
        if account is None:
            print(rx.match[1], 'account not found')
            return None
        rest = rx.match[2]
        hash, index, rest = get_params(rest)
        strict, rest = get_bool(rest)
        hotwallet, rest = get_array(rest)
        request = gateway_balances_request(account, hash, index, strict, hotwallet)
        res = send_request(request, node, port)
        print(do_format(pprint.pformat(res['result'])))
        return True
    return False




def set_issue(line):
    global issuers
    rx = Re()
    if rx.search(r'^\s*set\s+issue\s+([^\s]+)\s+([^\s]+)\s*$', line):
        issuers[rx.match[1]] = rx.match[2]
        with open('issuers.json', 'w') as f:
            json.dump(issuers, f)
        return True
    return False

# re-execute last command
def last(line):
    rx = Re()
    if rx.search(r'^\s*last\s*$', line):
        exec_command(history[-1])
        return True
    return False

def flags(line):
    rx = Re()
    if rx.search(r'^\s*flags\s*$', line):
        for k in validFlags.keys():
            sys.stdout.write(f'{k} ')
        sys.stdout.write('\n')
        return True
    return False

def load_accounts(line):
    rx = Re()
    if rx.search(r'^\s*load\s+accounts\s+([^\s]+)\s+([^\s]+)\s*$', line):
        file = rx.match[1]
        accts = rx.match[2]
        i = 1
        with open(file, 'r') as f:
            for j in json.load(f):
                accounts[f'{accts}{i}'] = {'id': j['classic_address'], 'seed': j['seed']}
                i += 1
        with open('accounts.json', 'w') as f:
            json.dump(accounts, f)
        return True
    return False

def server_info(line):
    rx = Re()
    if rx.search(r'^\s*server\s+info\s*$', line):
        res = send_request('{"method":"server_info"}', node, port)
        print(do_format(pprint.pformat(res['result'])))
        return True
    return False

# amm vote lp hash feevalue
def amm_vote(line):
    rx = Re()
    if rx.search(r'\s*amm\s+vote\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)\s*$', line):
        account = rx.match[1]
        account_id = getAccountId(account)
        if account_id is None:
            print(account, 'account not found')
            return False
        issues = getAMMIssues(rx.match[2])
        if issues is None:
            print(rx.match[2], 'tokens not found')
            return False
        feeVal = int(rx.match[3])
        request = vote_request(accounts[account]['seed'], account_id, issues, feeVal)
        res = send_request(request, node, port)
        error(res)
        return True
    return False

# amm bid lp hash (min|max) price [acct1,acct2]
def amm_bid(line):
    rx = Re()
    if rx.search(r'\s*amm\s+bid\s+([^\s]+)\s+([^\s]+)\s+(min|max)\s+([^\s]+)(\s+([^\s+]+))?\s*$', line):
        account = rx.match[1]
        account_id = getAccountId(account)
        if account_id is None:
            print(account, 'account not found')
            return False
        issues = getAMMIssues(rx.match[2])
        if issues is None:
            print(rx.match[2], 'tokens not found')
            return False
        pricet = rx.match[3]
        issue = getAMMIssue(rx.match[2])
        bid = Amount(issue, float(rx.match[4]))
        authAccounts = None
        if rx.match[6] is not None:
            authAccounts = rx.match[6].split(',')
        request = bid_request(accounts[account]['seed'], account_id, issues, pricet, bid, authAccounts)
        res = send_request(request, node, port)
        error(res)
        return True
    return False

def amm_hash(line):
    rx = Re()
    if rx.search(r'\s*amm\s+hash\s+([^\s]+)\s*$', line):
        hash = getAMMHash(rx.match[1])
        if hash == rx.match[1]:
            print(rx.match[1], 'hash not found')
            return False
        print(hash)
        return True
    return False

# expect amm hash none
# expect amm hash account lptoken
# expect amm token1 token2 [lptoken]
def expect_amm(line):
    def proc(token1, token2, token):
        amToken1 = Amount.fromStr(token1)
        if amToken1 is None:
            print('invalid amount', token1)
            return None
        amToken2 = Amount.fromStr(token2)
        if amToken2 is None:
            print('invalid amount', token2)
            return None
        lpToken = None
        if token is not None:
            lpToken = token
        request = amm_info_request(None, amToken1.issue, amToken2.issue, index='validated')
        res = send_request(request, node, port)
        if 'error' in res['result'] or not 'amm' in res['result']:
            raise Exception(f'{line.rstrip()}: {res["result"]["error"]}')
        asset1 = Amount.fromJson(res['result']['amm']['amount'])
        asset2 = Amount.fromJson(res['result']['amm']['amount2'])
        token = res['result']['amm']['lp_token']['value']

        def ne(asset: Amount) -> bool :
            if asset.issue == asset1.issue:
                return asset.value != asset1.value
            if asset.issue == asset2.issue:
                return asset.value != asset2.value
            return True

        if ne(amToken1) or ne(amToken2) or (lpToken is not None and lpToken != token):
            tokens = '' if lpToken is None else f'{lpToken},{token}'
            raise Exception(f'{line.strip()}: ##FAILED## {amToken1.toStr()},{asset1.toStr()},'
                            f'{amToken2.toStr()},{asset2.toStr()},'
                            f'{tokens}')

    rx = Re()
    if rx.search(r'^\s*expect\s+amm\s+([^\s]+)\s+none\s*$', line):
        issues = getAMMIssues(rx.match[1])
        if issues == rx.match[1]:
            print(rx.match[1], 'tokens not found')
            return None
        request = amm_info_request(None, issues[0], issues[1], index='validated')
        res = send_request(request, node, port)
        if res['result']['error'] != 'actNotFound':
            raise Exception(f'{line.rstrip()}: ##FAILED## {res}')
        return True
    # expect amm hash account lptoken | expect amm token1 token2 lptoken?
    elif rx.search(r'^\s*expect\s+amm\s+([^\s]+)\s+([^\s]+)(\s+(\d+(\.\d+)?))?\s*$', line):
        if getAccountId(rx.match[2]) is not None:
            issues = getAMMIssues(rx.match[1])
            if issues == rx.match[1]:
                print(rx.match[1], 'tokens not found')
                return None
            account = rx.match[2]
            account_id = getAccountId(account)
            if account_id is None:
                print(account, 'account not found')
                return None
            tokens = rx.match[4]
            if tokens is None:
                print('tokens must be specified')
                return None
            request = amm_info_request(account_id, issues[0], issues[1], index='validated')
            res = send_request(request, node, port)
            if tokens != res['result']['amm']['lp_token']['value']:
                raise Exception(f'{line.rstrip()}: ##FAILED## {res["result"]["LPToken"]["value"]}')
        else:
            proc(rx.match[1], rx.match[2], rx.match[4])
        return True
    return False

# expect fee hash fee
def expect_trading_fee(line):
    rx = Re()
    if rx.search(r'^\s*expect\s+fee\s+([^\s]+)\s+(\d+)\s*$', line):
        issues = getAMMIssues(rx.match[1])
        if issues == rx.match[1]:
            print(rx.match[1], 'tokens not found')
            return None
        fee = rx.match[2]
        request = amm_info_request(None, issues[0], issues[1], index='validated')
        res = send_request(request, node, port)
        if res['result']['amm']['trading_fee'] != int(fee):
            raise Exception(f'{line.rstrip()}: ##FAILED## {res["result"]["amm"]["trading_fee"]}')
        return True
    return False

# expect line account amount
def expect_line(line):
    rx = Re()
    if rx.search(r'\s*expect\s+line\s+([^\s]+)\s+(.+)\s*$', line):
        account = rx.match[1]
        account_id = getAccountId(account)
        if account_id is None:
            print(account, 'account not found')
            return None
        eamount = rx.match[2]
        if rx.search(r'^\s*\((.+)\)\s*$', eamount):
            asset = eval_expect_expr(rx.match[1])
        elif rx.search(r'^\s*\$([^\s]+)\s*$', eamount):
            asset = store[rx.match[1]]
        else:
            asset = Amount.fromStr(eamount)
        if asset is None:
            print('invalid amount', rx.match[2])
            return None
        request = account_trust_lines_request(account_id)
        res = send_request(request, node, port)
        found = False
        for l in res['result']['lines']:
            lineAsset = Amount.fromLineJson(l)
            if lineAsset == asset:
                found = True
                break
        if not found:
            raise Exception(f'{line.rstrip()}: ##FAILED## {res["result"]["lines"]}')
        return True
    return False

# expect offer account {takerPays, takerGets}, {takerPays1, takerGets}
def expect_offers(line):
    rx = Re()
    if rx.search(r'^\s*expect\s+offers\s+([^\s]+)(\s+(.+))?\s*$', line):
        account = rx.match[1]
        account_id = getAccountId(account)
        if account_id is None:
            print(account, 'account not found')
            return None
        offers_str = rx.match[3]
        offers = []
        offers_str_ = ''
        if offers_str is not None and not rx.search(r'^\s+$', offers_str):
            for o in re.split(r'\}\s*,\s*\{', offers_str):
                o_ = re.sub(r'[\{\}]', '', o)
                if rx.search(r'^\s*([^\s]+)\s*,\s*([^\s]+)\s*$', o_):
                    takerPays = Amount.fromStr(rx.match[1])
                    if takerPays is None:
                        print('invalid amount', rx.match[1])
                        return None
                    takerGets = Amount.fromStr(rx.match[2])
                    if takerGets is None:
                        print('invalid amount', rx.match[2])
                        return None
                    offers.append((takerPays, takerGets))
                    offers_str_ += f',({takerPays.toStr()},{takerGets.toStr()})'
        size = len(offers)
        request = account_offers_request(account_id)
        res = send_request(request, node, port)
        offers_j = res['result']['offers']
        if len(offers_j) != size:
            raise Exception(f'{line.rstrip()}: ##FAILED## {len(offers_j)},{size},{offers_j}')
        for offer in offers_j:
            takerPays = Amount.fromJson(offer['taker_pays'])
            takerGets = Amount.fromJson(offer['taker_gets'])
            offer1 = [o for o in offers if o == (takerPays, takerGets)]
            if offer1 is None or len(offer1) != 1:
                raise Exception(f'{line.rstrip()}: ##FAILED## {offers_str_} {offers_j}')
        return True
    return False

# this is account root balance
# expect account balance
def expect_balance(line):
    rx = Re()
    if rx.search(r'^\s*expect\s+balance\s+([^\s]+)\s+([^\s]+)\s*$', line):
        account = rx.match[1]
        account_id = getAccountId(account)
        if account_id is None:
            print(account, 'account not found')
            return None
        ebalance = rx.match[2]
        # get from the store
        if rx.search(r'^\$([^\s]+)$', ebalance):
            xrp = store[rx.match[1]]
        else:
            xrp = Amount.fromStr(ebalance)
        if xrp is None:
            print('invalid amount', rx.match[2])
            return None
        request = account_info_request(account_id)
        res = send_request(request, node, port)
        balance = Amount.fromStr(f'{res["result"]["account_data"]["Balance"]}XRP')
        if balance != xrp:
            raise Exception(f'{line.rstrip()}: ##FAILED## {balance.toStr()}')
        return True
    return False

def wait(line):
    rx = Re()
    if rx.search(r'^\s*wait\s+(\d+)\s*$', line):
        t = int(rx.match[1])
        time.sleep(t)
        return True
    return False

def run_script(line):
    rx = Re()
    if rx.search(r'^\s*run\s+([^\s]+)\s*$', line):
        with open(rx.match[1], 'r') as f:
            for h in json.load(f):
                print(h)
                if h[0] != '#':
                    exec_command(h)
        return True
    return False

# var1[+|-|*]var2
# var1,var2 : number|$store
def eval_expect_expr(expr):
    rx = Re()
    if rx.search(r'^\s*([^\s]+)\s*(\+|\-|\*)\s*([^\s]+)$', expr):
        var1 = get_store(rx.match[1], float)
        op = rx.match[2]
        var2 = get_store(rx.match[3], float)
        if op == '+':
            return var1 + var2
        if op == '-':
            return var1 - var2
        if op == '*':
            return var1 * var2
    else:
        return None

# repeat start end+1 cmd
# cmd can have expression (n*$i), when n must be int number
# $i is the current iter and can be concatenated with other params
# it can also contain padding format
# for instance,
# repeat 1 3 trust set (100*$i)A$2i gw
# results in three commands:
# trust set 100A01 gw
# trust set 200A02 gw
# trust set 300A03 gw
def repeat_cmd(line):
    rx = Re()
    if rx.search(r'^\s*repeat\s+(\d+)\s+(\d+)\s+(.+)$', line):
        start = int(rx.match[1])
        end = int(rx.match[2])
        # original expression that doesn't change
        cmd_ = rx.match[3]
        # evaluated expression
        cmd__ = cmd_
        for i in range(start, end + 1):
            if rx.search(r'\((\d+)\s*\*\s*\$i\)', cmd_):
                expr = int(rx.match[1]) * i
                cmd__ = re.sub(r'\((.+)\)', str(expr), cmd_)
            r = r'\$i'
            i_ = str(i)
            if rx.search(r'\$(\d)i', cmd__):
                i_ = '{num:0{width}}'.format(num=i, width=int(rx.match[1]))
                r = r'\$(\d)i'
            cmd = re.sub(r, i_, cmd__)
            exec_command(cmd)
        return True
    return False

# expect auction hash fee timeInterval price
def expect_auction(line):
    rx = Re()
    if rx.search(r'^\s*expect\s+auction\s+([^\s]+)\s+(\d+)\s+(\d+)\s+([^\s]+)\s*$', line):
        issues = getAMMIssues(rx.match[1])
        if issues == rx.match[1]:
            print(rx.match[1], 'tokens not found')
            return None
        fee = int(rx.match[2])
        interval = int(rx.match[3])
        price = rx.match[4]
        request = amm_info_request(None, issues[0], issues[1], index='validated')
        res = send_request(request, node, port)
        slot = res['result']['amm']['auction_slot']
        efee = slot['discounted_fee']
        eprice = slot['price']['value']
        einterval = slot['time_interval']
        if efee != fee or einterval != interval or eprice != price:
            raise Exception(f'{line.rstrip()}: failed {fee},{einterval},{eprice}')
        return True
    return False

# get account balance and save into store[var]
# get balance account var
def get_balance(line):
    global store
    rx = Re()
    if rx.search(r'^\s*get\s+balance\s+([^\s]+)\s+([^\s]+)\s*$', line):
        account = rx.match[1]
        account_id = getAccountId(account)
        if account_id is None:
            print(account, 'account not found')
            return None
        var = rx.match[2]
        request = account_info_request(account_id)
        res = send_request(request, node, port)
        store[var] = Amount.fromStr(f'{res["result"]["account_data"]["Balance"]}XRP')
        return True
    return False

# get account line and save into store[var]
# get line account currency var
def get_line(line):
    global store
    rx = Re()
    if rx.search(r'^\s*get\s+line\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)\s*$', line):
        account = rx.match[1]
        account_id = getAccountId(account)
        if account_id is None:
            print(account, 'account not found')
            return None
        issue = Issue.fromStr(rx.match[2])
        var = rx.match[3]
        request = account_trust_lines_request(account_id)
        res = send_request(request, node, port)
        store[var] = None
        for l in res['result']['lines']:
            asset = Amount.fromLineJson(l)
            if asset.issue.currency == issue.currency:
                store[var] = asset
        return True
    return False

def set_wait(line):
    global tx_wait
    rx = Re()
    if rx.search(r'^\s*set\s+wait\s+([^\s]+)\s*$', line):
        tx_wait = float(rx.match[1])
        return True
    return False

def clear_store(line):
    global store
    rx = Re()
    if rx.search(r'^\s*clear\s+store\s*$', line):
        store = {}
        return True
    return False

def toggle_pprint(line):
    global do_pprint
    rx = Re()
    if rx.search(r'^\s*pprint\s+(on|off)\s*$', line):
        if rx.match[1] == 'on':
            do_pprint = True
        else:
            do_pprint = False
        return True
    return False

def tx_lookup(line):
    rx = Re()
    if rx.search(r'^\s*tx\s+([^\s]+)(.*)$', line):
        txid = rx.match[1]
        if txid[0] == '$':
            txid = store[txid[1:]]
        hash, index, rest = get_params(rx.match[2])
        if hash is not None:
            request = tx_request(txid, lhash=hash)
        else:
            request = tx_request(txid, index=index)
        res = send_request(request, node, port)
        # check if a filter is included to print specified ledger entries
        filter, rest = make_objects_filter(rest)
        if filter is not None:
            try:
                meta = []
                for m in res['result']['meta']['AffectedNodes']:
                    # k is CreatedNode,ModifiedNode,DeletedNode
                    for k, v in m.items():
                        # filter is for the FinalFields
                        if ('LedgerEntryType' in filter and
                                v['LedgerEntryType'] not in filter['LedgerEntryType']):
                            # don't include
                            break
                        obj = {}
                        obj[k] = {}
                        obj[k]['LedgerEntryType'] = v['LedgerEntryType']
                        # only include FinalFields with the columns and
                        # all PreviousFields
                        final_fields = {}
                        fields_key = 'FinalFields' if k != 'CreatedNode' else 'NewFields'
                        for k1, v1 in m[k][fields_key].items():
                            if 'All' in filter or (k1 in filter and (filter[k1] is None or v1 in filter[k1])):
                                final_fields[k1] = v1
                        obj[k][fields_key] = final_fields
                        if fields_key != 'NewFields':
                            obj[k]['PreviousFields'] = m[k]['PreviousFields']
                        meta.append(obj)
                # only retain the "interesting" fields
                try:
                    del res['result']['Sequence']
                    del res['result']['SigningPubKey']
                    del res['result']['TxnSignature']
                    del res['result']['ctid']
                    del res['result']['date']
                    del res['result']['inLedger']
                    del res['result']['ledger_index']
                    del res['result']['meta']['TransactionIndex']
                except:
                    pass
                def sort_helper(m):
                    if 'ModifiedNode' in m:
                        return m['ModifiedNode']['FinalFields']['Account']
                    elif 'DeletedNode' in m:
                        return m['DeletedNode']['FinalFields']['Account']
                    return m['CreatedNode']['NewFields']['Account']

                # sort by Account if included
                #if 'Account' in filter:
                #    meta = sorted(meta, key=sort_helper)
                res['result']['meta']['AffectedNodes'] = meta
            except:
                pass
        print(do_format(pprint.pformat(res)))
        return True
    return False

def tx_history(line):
    rx = Re()
    if rx.search(r'^\s*txhistory(\s+(\d)\s*)?$', line):
        start = rx.match[2]
        if start is None:
            start = 0
        request = tx_history_request(start)
        res = send_request(request, node, port)
        print(do_format(pprint.pformat(res)))
        return True
    return False

def server_state(line):
    rx = Re()
    if rx.search(r'^\s*server\s+state\s*$', line):
        res = send_request('{"method":"server_state"}', node, port)
        print(do_format(pprint.pformat(res)))
        return True
    return False

# only gets "amm" or oracle object, more general ledger_entry would require separate
# command for each type
# ledger entry amm XRP USD | object_id
# ledger entry oracle account documentid index
def ledger_entry(line):
    line = re.sub(r'ledgerentry', 'ledger entry', line)
    rx = Re()
    #ledger entry oracle account documentid #hash @index
    if rx.search(r'^\s*ledger\s+entry\s+oracle\s+([^\s]+)\s+([^\s]+)(.*)$', line):
        account = rx.match[1]
        id = rx.match[2]
        hash, index, rest = get_params(rx.match[3])
        req = ledger_entry_oracle_request(accounts[account]['id'], id, index, hash)
        res = send_request(req, node, port)
        print(do_format(pprint.pformat(res)))
        return True
    # ledger entry object_id
    elif rx.search(r'^\s*ledger\s+entry\s+amm\s+([^\s]+)(.*)$', line):
        id = rx.match[1]
        hash, index, rest = get_params(rx.match[2])
        req = ledger_entry_request(id=id, index=index, hash=hash)
        res = send_request(req, node, port)
        print(do_format(pprint.pformat(res)))
        return True
    # ledger entry asset asset2
    elif rx.search(r'^\s*ledger\s+entry\s+amm\s+([^\s]+)\s+([^\s]+)(.*)$', line):
        asset = Issue.fromStr(rx.match[1])
        if asset is None:
            print('Invalid asset', asset)
            return None
        asset2 = Issue.fromStr(rx.match[2])
        if asset2 is None:
            print('Invalid asset', asset2)
            return None
        hash, index, rest = get_params(rx.match[3])
        req = ledger_entry_request(asset=asset, asset2=asset2, index=index, hash=hash)
        res = send_request(req, node, port)
        print(do_format(pprint.pformat(res)))
        return True
    elif rx.search(r'^\s*ledger\s+entry\s+mpt\s+([^\s]+)(.*)$', line):
        if rx.match[1] in mpts:
            mpt_id = mpts[rx.match[1]]
        else:
            mpt_id = rx.match[1]
        hash, index, rest = get_params(rx.match[2])
        account = rest if rest is not None and rx.search(r'^\s$', rest) is None else None
        account_id = None
        if account:
            account = account.replace(' ', '')
            account_id = getAccountId(account)
            if account_id is None:
                print(account, 'not found')
                return None
        req = ledger_entry_mpt_request(mpt_id, account_id, index, hash)
        res = send_request(req, node, port)
        print(do_format(pprint.pformat(res)))
        return True
    return False

# ledger data [#hash] [@index] [$limit] [%binary] [^marker] [type]
def ledger_data(line):
    rx = Re()
    if rx.search(r'^\s*ledger\s+data\s+(.+)\s*$', line):
        rest = rx.match[1]
        type_ = None
        hash, index, rest = get_params(rest, 'validated')
        limit, marker, binary, rest = get_params_ext(rest)
        rest = re.sub(r'\s+', '', rest)
        if rest != '':
            type_ = rest
        req = ledger_data_request(hash=hash, index=index, limit=limit, binary=binary, marker=marker, type_=type_)
        res = send_request(req, node, port)
        print(do_format(pprint.pformat(res)))
        return True
    return False

def ledger_accept(line):
    rx = Re()
    if rx.search(r'^\s*ledger\s+accept\s*$', line):
        res = send_request('{"method": "ledger_accept"}', node, port)
        error(res)
        return True
    return False

# account objects account [#hash] [@index] [$limit] [^maker] [delete_only] [type] [field1,...]
def account_objects(line):
    rx = Re()
    if rx.search(r'^\s*account\s+objects\s+([^\s]+)(.*)\s*$', line):
        account = getAccountId(rx.match[1])
        if account is None:
            print(rx.match[1], 'account not found')
            return None
        rest = rx.match[2]
        # list of account objects fields to output
        filter, rest = make_objects_filter(rest)
        type_ = None
        hash, index, rest = get_params(rest, 'validated')
        limit, marker, delete_only, rest = get_params_ext(rest)
        rest = re.sub(r'\s+', '', rest)
        if rest != '':
            type_ = rest
        req = account_objects_request(account, hash=hash, index=index, limit=limit, marker=marker,
                                      delete_only = delete_only, type_ = type_)
        res = send_request(req, node, port)
        if filter is not None:
            account_objects = []
            for o in res['result']['account_objects']:
                obj = {}
                for k, v in o.items():
                    if k in filter:
                        # if filter value doesn't match then skip this obj
                        if filter[k] is not None:
                            if v not in filter[k]:
                                obj = None
                                break
                            # don't include in the object if it matches, we know it's value
                        else:
                            obj[k] = v
                if obj is not None:
                    account_objects.append(obj)
            #res['result']['account_objects'] = account_objects
            res = account_objects
        print(do_format(pprint.pformat(res)))
        return True
    return False

# noripple check account role transactions $limit #hash @index
def noripple_check(line):
    rx = Re()
    if rx.search(r'^\s*noripple\s+check\s+([^\s]+)\s+([^\s]+)(.+)$', line):
        account = getAccountId(rx.match[1])
        if account is None:
            print(rx.match[1], 'account not found')
            return None
        role = rx.match[2]
        rest = rx.match[3]
        hash, index, rest = get_params(rest)
        transactions, rest = get_bool(rest)
        limit, rest = get_with_prefix(r'\$', rest)
        req = noripple_check_request(account, role, transactions, limit, hash, index)
        res = send_request(req, node, port)
        print(do_format(pprint.pformat(res)))
        return True
    return False



# book offers taker_pays [gw] taker_gets [gw] [limit] [[field1,field2,...]]
def book_offers(line):
    rx = Re()
    if rx.search(r'^\s*book\s+offers\s+(.+)$', line):
        fields = None
        rest = rx.match[1]
        if rx.search(r'\[([^\s]+)\]', rest):
            fields = rx.match[1].split(',')
            rest = re.sub(r'\[([^\s]+)\]', '', rest)
        taker_pays, rest = Issue.nextFromStr(rest)
        if taker_pays is None:
            # try with issuer
            taker_pays, rest = Issue.nextFromStr(rest, True)
            if taker_pays is None:
                print('invalid taker pays', rest)
                return None
        taker_gets, rest = Issue.nextFromStr(rest)
        if taker_gets is None:
            # try with issuer
            taker_gets, rest = Issue.nextFromStr(rest, True)
            if taker_gets is None:
                print('invalid taker gets', rest)
                return None
        limit = 10
        if rx.search(r'(\d+)', rest):
            limit = int(rx.match[1])
        request = book_offers_request(taker_pays, taker_gets, limit)
        res = send_request(request, node, port)
        if fields is None:
            print(do_format(pprint.pformat(res['result'])))
        else:
            l = list()
            for offer in res['result']['offers']:
                j = dict()
                for field in fields:
                    if field in offer:
                        j[field] = offer[field]
                l.append(j)
            print(do_format(pprint.pformat(l)))
        return True
    return False

# path find src dest dest_amount [send_max] [[currencies]]
def path_find(line):
    rx = Re()
    if rx.search(r'^\s*path\s+find\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)(.*)$', line):
        src = getAccountId(rx.match[1])
        if src is None:
            print('invalid account', rx.match[1])
            return None
        dst = getAccountId(rx.match[2])
        if dst is None:
            print('invalid account', rx.match[2])
            return None
        dst_amount,rest = Amount.nextFromStr(rx.match[3])
        send_max,rest = Amount.nextFromStr(rx.match[4])
        src_curr = None
        if rx.search(r'\[([^\s]+)\]', rest):
            src_curr = rx.match[1].split(',')
        request = path_find_request(src, dst, dst_amount, send_max, src_curr)
        res = send_request(request, node, port)
        print(do_format(pprint.pformat(res['result'])))
        return True
    return False

# oracle set account docid [base quote price scale,...]
def oracle_set(line):
    rx = Re()
    if rx.search(r'^\s*oracle\s+set\s+([^\s]+)\s+([^\s]+)\s+\[([^\]]+)\]\s*$', line):
        account = rx.match[1]
        account_id = getAccountId(account)
        if account_id is None:
            print('invalid account', rx.match[1])
            return None
        id = rx.match[2]
        data_series = []
        for data in rx.match[3].split(','):
            data_series.append(data.split(' '))
        request = oracle_set_request(accounts[account]['seed'], account_id, id, data_series)
        res = send_request(request, node, port)
        error(res)
        return True
    return False

def oracle_delete(line):
    rx = Re()
    if rx.search(r'^\s*oracle\s+delete\s+([^\s]+\s+([^\s]+)\s*$)', line):
        account = rx.match[1]
        account_id = getAccountId(account)
        if account_id is None:
            print('invalid account', rx.match[1])
            return None
        id = rx.match[2]
        request = oracle_delete_request(accounts[account]['seed'], account_id, id)
        res = send_request(request, node, port)
        error(res)
        return True
    return False

def oracle_aggregate(line):
    rx = Re()
    if rx.search(r'^\s*oracle\s+aggregate\s+([^\s]+)\s+([^\s]+)\s+\[([^\]]+)\]$', line):
        base_asset = rx.match[1]
        quote_asset = rx.match[2]
        oracles = []
        for oracle in rx.match[3].split(','):
            account, id = oracle.split(' ')
            account = getAccountId(account);
            if account is None:
                print(rx.match[1], 'not found')
                return None
            oracles.append([account, id])
        request = get_aggregate_price_request(base_asset, quote_asset, oracles)
        res = send_request(request, node, port)
        print(do_format(pprint.pformat(res)))
        return True
    return False

def ledger(line):
    rx = Re()
    if rx.search(r'^\s*ledger(.+)$', line):
        hash, index, rest = get_params(rx.match[1])
        if ((hash is not None) + (index is not None)) != 1:
            print('ledger hash or index must be specified')
            return None
        request = get_ledger_request(hash, index)
        res = send_request(request, node, port)
        print(do_format(pprint.pformat(res)))
        return True
    return False

# mpt create account alias [maxAmt=] [scale=] [tfee=] [meta=] [flags]
def mpt_create(line):
    rx = Re()
    if rx.search(r'^\s*mpt\s+create\s+([^\s]+)\s+([^\s]+)(.*)$', line):
        account = rx.match[1]
        account_id = getAccountId(account)
        if account_id is None:
            print(rx.match[1], 'not found')
            return None
        mpt_alias = rx.match[2]
        #if mpt_alias in mpts:
        #    print(mpt_alias, 'already defined')
        #    return None
        rest = rx.match[3]
        maxAmt = None
        scale = None
        tfee = None
        meta = None
        flags = None
        if rest is not None and rx.search(r'maxAmt=(\d+)', rest):
            maxAmt = rx.match[1]
            rest = re.sub(r'maxAmt=\d+', '', rest)
        if rest is not None and rx.search(r'scale=(\d+)', rest):
            scale = rx.match[1]
            rest = re.sub(r'scale=\d+', '', rest)
        if rest is not None and rx.search(r'tfee=(\d+)', rest):
            tfee = rx.match[1]
            rest = re.sub(r'tfee=\d+', '', rest)
        if rest is not None and rx.search(r'meta=(\d+)', rest):
            meta = rx.match[1]
            rest = re.sub(r'meta=\d+', '', rest)
        if rest is not None:
            flags = getFlags(rest, 0)
        # get sequence
        request = account_info_request(account_id, 'current')
        res = send_request(request, node, port)
        seq = res['result']['account_data']['Sequence']
        acct = binascii.b2a_hex(decode_classic_address(account_id)).decode("utf-8")
        mpt_id = f'{seq:08x}{acct}'.upper()
        request = get_mpt_create_request(accounts[account]['seed'], account_id, maxAmt, scale, tfee, meta, flags)
        res = send_request(request, node, port)
        if error(res) == False:
            mpts[mpt_alias] = mpt_id
            mpts_alias[mpt_id] = mpt_alias
            dump_mpts()
        return True
    return False

# mpt authorize account mptid [holder] [flags]
def mpt_authorize(line):
    rx = Re()
    if rx.search(r'^\s*mpt\s+authorize\s+([^\s]+)\s+([^\s]+)(.*)$', line):
        account = rx.match[1]
        account_id = getAccountId(account);
        if account_id is None:
            print(rx.match[1], 'not found')
            return None
        mpt_alias = rx.match[2]
        if not mpt_alias in mpts:
            print(rx.match[2], 'not found')
            return None
        mpt_id = mpts[mpt_alias]
        rest = rx.match[3]
        holder = None
        flags = 0
        if rest is not None:
            # holder and flags
            if rx.search(r'^\s*([^\s]+)\s+([^\s]+)\s*$', rest):
                holder = rx.match[1]
                holder_id = getAccountId(holder)
                if holder_id is None:
                    print(holder, 'not found')
                    return None
            # holder or flags
            elif rx.search(r'^\s*([^\s]+)\s*$', rest):
                holder = rx.match[1]
                holder_id = getAccountId(holder)
                if holder_id is None:
                    holder = None
                    flags = getFlags(rx.match[1], 0)
        request = get_mpt_auth_request(accounts[account]['seed'], account_id, mpt_id, holder, flags)
        res = send_request(request, node, port)
        error(res)
        return True
    return False


#mpt set account mptid [holder] [flags],
def mpt_set(line):
    rx = Re()
    if rx.search(r'^\s*mpt\s+set\s+([^\s]+)\s+([^\s]+)(.*)$', line):
        account = rx.match[1]
        account_id = getAccountId(account);
        if account_id is None:
            print(rx.match[1], 'not found')
            return None
        mpt_alias = rx.match[2]
        if not mpt_alias in mpts:
            print(rx.match[2], 'not found')
            return None
        mpt_id = mpts[mpt_alias]
        rest = rx.match[3]
        holder = None
        flags = None
        if rest is not None:
            # holder and flags
            if rx.search(r'^\s*([^\s]+)\s+([^\s]+)\s*$', rest):
                holder = rx.match[1]
                holder_id = getAccountId(holder)
                if holder_id is None:
                    print(holder, 'not found')
                    return None
            # holder or flags
            elif rx.search(r'^\s*([^\s]+)\s*$', rest):
                holder = rx.match[1]
                holder_id = getAccountId(holder)
                if holder_id is None:
                    holder = None
                    flags = getFlags(rx.match[1], 0)
        if flags is None or flags == 0:
            print('flags must be specified')
            return None
        request = get_mpt_set_request(accounts[account]['seed'], account_id, mpt_id, holder, flags)
        res = send_request(request, node, port)
        error(res)
        return True
    return False

#mpt destroy account mptid
def mpt_destroy(line):
    rx = Re()
    if rx.search(r'^\s*mpt\s+destroy\s+([^\s]+)\s+([^\s]+)\s*$', line):
        account = rx.match[1]
        account_id = getAccountId(account);
        if account_id is None:
            print(rx.match[1], 'not found')
            return None
        mpt_alias = rx.match[2]
        if not mpt_alias in mpts:
            print(rx.match[2], 'not found')
            return None
        mpt_id = mpts[mpt_alias]
        request = get_mpt_destroy_request(accounts[account]['seed'], account_id, mpt_id)
        res = send_request(request, node, port)
        error(res)
        return True
    return False

commands = {}
account_commands = {}
amm_commands = {}
offer_commands = {}
oracle_commands = {}
mpt_commands = {}
ledger_commands = {}

# show available commands
def help(line):
    rx = Re()
    if rx.search(r'^\s*help(\s+([^\s]+)(\s+([^\s]+))?)?\s*$', line):
        if rx.match[1] is None:
            for k,c in commands.items():
                print(c[1])
        elif rx.match[2] is not None and rx.match[4] is not None:
            k1 = rx.match[2]
            k2 = rx.match[4]
            k = k1 + k2
            if k in commands:
                print(commands[k][1])
            elif k1 in commands:
                if k2 in account_commands:
                    print(account_commands[k2][1])
                elif k2 in amm_commands:
                    print(amm_commands[k2][1])
                elif k2 in offer_commands:
                    print(offer_commands[k2][1])
                elif k2 in oracle_commands:
                    print(oracle_commands[k2][1])
                elif k2 in mpt_commands:
                    print(mpt_commands[k2][1])
                else:
                    print("can't find:", line)
                    return None
            else:
                print("can't find:", line)
                return None
        elif rx.match[2] is not None and rx.match[2] in commands:
            print(commands[rx.match[2]][1])
        else:
            print("can't find:", line)
            return None
    return True


account_commands = {'ClearFlag': [account_set, "account ClearFlag acct1,... [flags]: set flag"],
                    'channels': [account_channels, "account channels account #hash @index: account channels"],
                    'currencies': [account_currencies, "account currencies account strict #hash @index: account currencies"],
                    'delete': [account_delete, "account delete acct destacct [tag]: delete account"],
                    'info': [account_info, "account info acct: account info"],
                    'lines': [account_lines, "account lines acct [[cur1,...]]: account lines for the currencies(optional)"],
                    'nfts': [account_nfts, "account nfts acct #hash @index: account nfts"],
                    'objects': [account_objects, "account objects acct: account objects"],
                    'offers': [account_offers, "account offers acct #hash @index: account offers"],
                    'SetFlag': [account_set, "account SetFlags acct1,... [flags]"],
                    'tx': [account_tx, "tx txid: lookup transaction"]}
offer_commands = {'create': [offer_create, "offer create acct takerPaysAmt [gw] takerGetsAmt [gw]: offer create"],
                  'cancel': [offer_cancel, "offer cancel acct [seq1,...]: cancel all offers or specific offer seq"]}
amm_commands = {'bid': [amm_bid, "amm bid lp hash (min|max) price [acct1,acct2]: amm bid by lp to amm hash"],
                'create': [amm_create, "amm create [@alias] account currency currency [trading fee]: amm create. can assign alias, which can be used in other commands requiring amm account or amm issue. if alias is not specified then the default name is ammCUR1-CUR2"],
                'deposit': [amm_deposit, """
                amm deposit account alias tokens
                amm deposit account alias asset1in
                amm deposit account alias asset1in asset2in [empty [tfee]]
                amm deposit account alias asset1in tokens
                amm deposit account alias asset1in @eprice: amm deposit options, alias is an alias for issue1, issue2 from amm create.
                """],
                'hash': [amm_hash, "amm hash alias: get amm hash, this is internal command"],
                'info': [amm_info, "amm info [currency1 currency2] [alias] [account] [[Amount,Amount2...]] [$ledger] [save key]: amm info either by token pair of amm alias. can specify the fields to display. can save into internal storage."],
                'withdraw': [amm_withdraw, """
                amm withdraw account alias tokens
                amm withdraw account alias asset1in
                amm withdraw account alias asset1in asset2in
                amm withdraw account alias asset1in tokens
                amm withdraw account alias asset1in @eprice: amm withdraw options, alias is an alias for issue1, issue2 from amm create.
                """],
                'vote': [amm_vote, "amm vote lp alias feevalue: amm vote"]}
oracle_commands = {
    'set': [oracle_set, "oracle set account docid base quote price scale"],
    'delete': [oracle_delete, "oracle delete account docid"],
    'aggregate': [oracle_aggregate, "oracle aggregate base_asset quote_asset [account id,...]"]}

mpt_commands = {
    'create': [mpt_create, 'mpt create account alias [maxAmt=] [scale=] [tfee=] [meta=] [flags=]'],
    'authorize': [mpt_authorize, 'mpt authorize account mptid [holder] [flags]'],
    'set': [mpt_set, 'mpt set account mptid holder [flags]'],
    'destroy': [mpt_destroy, 'mpt destroy account mptid'],
}

ledger_commands = {
    'ledger': [ledger, "ledger [#hash-ledger hash] [@index-ledger index]: ledger command"],
    'entry': [ledger_entry,
                    "ledger entry [amm|oracle|mpt] [token token2|AMM objectid|mpt_id]|[account id]: get amm/oracle object by token/token2 or ammid or account id"],
    'data': [ledger_data,
                   "ledger data [#hash-ledger hash] [@index-ledger index] [$limit-number of objects] [%binary-true|false] [^marker-marker] [type-object type]: get ledger data"],
    'accept': [ledger_accept, "ledger accept: close the ledger"],
}

def account_commands_(line):
    rx = Re()
    if rx.search(r'^\s*account\s+(objects|info|lines|offers|SetFlag|ClearFlag|delete|channels|currencies|nfts|tx)', line):
        return account_commands[rx.match[1]][0](line)
    if rx.search(r'^\s*gateway\s+balances', line):
        return gateway_balances(line)
    if rx.search(r'^\s*noripple\s+check', line):
        return noripple_check(line)
    return False

def offer_commands_(line):
    rx = Re()
    if rx.search(r'^\s*offer\s+(create|cancel)', line):
        return offer_commands[rx.match[1]][0](line)
    return False

def amm_commands_(line):
    rx = Re()
    if rx.search(r'^\s*amm\s+(create|deposit|withdraw|vote|bid|info|hash)', line):
        return amm_commands[rx.match[1]][0](line)
    return False

def oracle_commands_(line):
    rx = Re()
    if rx.search(r'^\s*oracle\s+(set|delete|aggregate)', line):
        return oracle_commands[rx.match[1]][0](line)
    return False

def mpt_commands_(line):
    rx = Re()
    if rx.search(r'^\s*mpt\s+(create|authorize|set|destroy)', line):
        return mpt_commands[rx.match[1]][0](line)
    return False

def ledger_commands_(line):
    rx = Re()
    if rx.search(r'^\s*ledger\s+(entry|data|accept)', line):
        return ledger_commands[rx.match[1]][0](line)
    elif rx.search(r'^\s*ledger\s+', line):
        return ledger_commands['ledger'][0](line)
    return False

def toggle_accept(line):
    global auto_accept
    rx = Re()
    if rx.search(r'^\s*auto\s+accept\s+(on|off)\s*$', line):
        auto_accept = True if rx.match[1] == 'on' else False
        return True
    return False


commands = {
    'autoaccept': [toggle_accept, "auto accept [on|off]: set ledger accept on/off after each submit"],
    'account': [account_commands_, "account [objects|info|lines|offers|SetFlag|delete|channels|currencies|nfts|tx]: account commands, type account command to get specific help"],
    'accounts': [show_accounts, "accounts [a1,a2,...,aN]: output all or specified accounts"],
    'amm': [amm_commands_, "amm [create|deposit|withdraw|vote|bid|info|hash]: amm commands, type amm command to get specific help"],
    'book': [book_offers, "book offers taker_pays [gw] taker_gets [gw] [limit] [[field1,field2,...]]: book offers"],
    'clearall': [clear_all, "clear all: clears internal data"],
    'clearhistory': [clear_history, "clear history: clear all history"],
    'clearstore': [clear_store, "clear store: clear internal saved variables"],
    'expectamm': [expect_amm, "expect amm token1 token2 [lptoken]: outputs an error if amm balances don't match"],
    'expectauction': [expect_auction, "expect auction ammAccount fee timeInterval price: outputs an error if amm auction slot params don't match"],
    'expectbalance': [expect_balance, "expect account balance: outputs an error if account root balance doesn't match"],
    'expectfee': [expect_trading_fee, "expect fee ammAccount fee: outputs an error if trading fee doesn't match"],
    'expectline': [expect_line, "expect line account amount: outputs an error if account's line balance doesn't match"],
    'expectoffers': [expect_offers, "expect offers account {takerPays, takerGets}, {takerPays1, takerGets}: outputs an error if account's offers don't match"],
    'flags': [flags, "flags: output valid flags"],
    'fund': [fund, "fund account[,account1,...] XRP: call wallet_create and pay from genesis into accounts"],
    'getbalance': [get_balance, "get account balance account var: get account balance and save into store[var]"],
    'getline': [get_line, "get line account currency var: get account line and save into store[var]"],
    'help': [help, "help [command1 [command2]]: output help, include command's one or two keys to get specific help"],
    'history': [do_history, "history [n1[-n2]]: get the history of commands, if command number or range is included then execute these commands"],
    'h': [do_history, "history [n1[-n2]]: get the history of commands, if command number or range is included then execute these commands"],
    'issuers': [show_issuers, "issuers: show issuer accounts"],
    'last': [last, "last: execute last history command"],
    'ledger': [ledger_commands_, "ledger [entry|data]: ledger commands"],
    'load': [load_accounts, "load accounts file name: loads accounts from json file as nameI, when I is the count"],
    'mpt': [mpt_commands_, "mpt [create|authorize|set|destroy]: mpt commands, type mpt command to get specific help"],
    'offer': [offer_commands_, "offer [create|cancel]: offer commands, type offer command to get specific help"],
    'oracle': [oracle_commands_, "oracle [set|delete] account docid base quote price scale"],
    'path': [path_find, "path find src dest dest_amount [send_max] [[cur1,..]]: call path find"],
    'pay': [pay, "pay src dst[,dst1,...] amount currency [[path1,path2...] sendmax]: send a payment, specify [] for default path"],
    'pprint': [toggle_pprint, "pprint on|off: user friendly print"],
    'repeat': [repeat_cmd, "repeat start end+1 cmd: repeat any valid command, for instance: repeat 1 3 trust set (100*$i)A$2i gw. results in three commands: trust set 100A01 gw, ..."],
    'run': [run_script, "run file: executes json commands from the file."],
    'session': [session_restore, "session restore: restores accounts from the previous session. this command is always called on start."],
    'serverinfo': [server_info, "server info: call server_info"],
    'serverstate': [server_state, "server state: call server_state"],
    'setaccount': [set_account, "set account name id seed: manually sets the account"],
    'setissue': [set_issue, "set issue currency issuer: mantually sets the issue"],
    'setnode': [set_node, "set node node:port: sets the node to connect to"],
    'setwait': [set_wait, "set wait t: sets the wait between transactions to t seconds"],
    'trust': [trust_set, "trust set account,account1,.. amount currency issuer [flags]: set the trust"],
    'tx': [tx_lookup, "tx hash: lookup tx metadata"],
    'txhistory': [tx_history, "tx_history start: lookup recent transactions"],
    'verbose': [toggle_verbose, "verbose on|off [hash]: prints json load"],
    'wait': [wait, "wait t: wait t seconds"]
}

def prompt():
    sys.stdout.write('> ')
    sys.stdout.flush()

def exec_command(line):
    rx = Re()
    if rx.search(r'^\s*$', line):
        return
    global commands
    res = None

    if rx.search(r'^\s*([^\s]+)(\s+([^\s]+))?', line):
        k1 = rx.match[1]
        k2 = (k1 + rx.match[3]) if rx.match[3] is not None else None
        try:
            if k1 in commands:
                res = commands[k1][0](line)
            elif k2 is None:
                res = print_account(line)
            elif k2 in commands:
                res = commands[k2][0](line)
        except Exception as ex:
            print(line, ex)
            res = None

    if res is not None and res:
        history.append(line.strip('\n'))
        with open('history.json', 'w') as f:
            json.dump(history, f)
    else:
        print('invalid command:', line)

'''
# Enable tab completion
readline.parse_and_bind("tab: complete")

# Set maximum history file size
readline.set_history_length(1000)

# Optionally, you can add custom completer functions
def completer(text, state):
    commands = ['help', 'exit', 'list', 'show']
    matches = [cmd for cmd in commands if cmd.startswith(text)]
    return matches[state] if state < len(matches) else None

readline.set_completer(completer)
'''

def main():
    while True:
        try:
            # Read input from the user
            command = input("> ")
            # Execute the command (here we just print it)
            exec_command(command)
        except EOFError:
            break
        except KeyboardInterrupt:
            # Handle interrupt signal (Ctrl+C)
            print("\nKeyboardInterrupt. Use ^D to exit.")
        except Exception as e:
            # Handle other exceptions
            print(f"Error: {e}")

def main_debug():
    prompt()
    for line in sys.stdin:
        exec_command(line)
        prompt()


if __name__ == "__main__":
    if script is not None:
        exec_command(f'run {script}')
    elif debug:
        main_debug()
    else:
        main()
