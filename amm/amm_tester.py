#!/usr/bin/env python3
import requests
import sys
import random
import json
import time
import re
import pprint
from collections import defaultdict

do_pprint = True
drops_per_xrp = 1_000_000
port = 51234
node = '127.0.0.1'
fund = False
script = None
store = {}
tx_wait = 0.1
tx_wait_save = tx_wait
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
    else:
        raise Exception(f'invalid argument {sys.argv[i]}')
    i += 1

genesis_acct = 'rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh'
genesis_sec = 'snoPBrXtMeMyMHUVTgbuqAfg1SUTb'
burn_acct = None
burn_sec = None
ammdevnet = 'http://amm.devnet.rippletest.net'

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

validFlags = {'noDirectRipple':65536, 'partialPayment':131072, 'limitQuality':262144,
             'passive':65536, 'immediateOrCancel':131072, 'fillOrKill': 262144,
             'sell': 524288, 'accountTxnID':5, 'authorizedNFTokenMinter': 10, 'defaultRipple': 8,
             'depositAuth': 9, 'disableMaster': 4, 'disallowXRP': 3, 'globalFreeze': 7, 'noFreeze': 6,
             'requireAuth': 2, 'requireDest': 1, 'withdrawAll': 0x20, 'noRippleDirect': 65536,
              'LPToken': 0x00010000, 'WithdrawAll': 0x00020000, 'OneAssetWithdrawAll': 0x00040000,
              'SingleAsset': 0x000080000, 'TwoAsset': 0x00100000, 'OneAssetLPToken': 0x00200000,
              'LimitLPToken': 0x00400000, 'setNoRipple': 0x00020000, 'clearNoRipple': 0x00040000,
              'TwoAssetIfEmpty': 0x00800000}

def load_history():
    global history
    try:
        with open('history.json', 'r') as f:
            history = json.load(f)
    except:
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

def dump_history():
    global history
    with open('history.json', 'w') as f:
        json.dump(history, f)

def dump_accounts():
    global accounts
    with open('accounts.json', 'w') as f:
        json.dump(accounts, f)

def dump_issuers():
    global issuers
    with open('issuers.json', 'w') as f:
        json.dump(issuers, f)

load_history()
load_accounts_()
load_issuers()

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
    if 'result' in res and 'engine_result' in res['result']:
        if res['result']['engine_result'] == 'tesSUCCESS':
            return False
        else:
            print('error:', res['result']['engine_result_message'])
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
    def __init__(self, currency, issuer = None):
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
        return self.currency == 'XRP'
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
            currency = rx.match[1].upper()
            rest = rx.match[2]
            if currency == 'XRP' or currency == 'XRPD':
                return (Issue(currency, None), rest)
            if with_issuer and rx.search(r'^\s+([^\s]+)(.*)$', rest):
                issuer = rx.match[1]
                rest = rx.match[2]
                id = getAccountId(issuer)
                if id is None:
                    return (None, s)
                return (Issue(currency, id), rest)
            if currency in issuers:
                return (Issue(currency, getAccountId(issuers[currency])), rest)
        return (None, s)
    def fromStr(s, with_issuer = False):
        (iou, rest) = Issue.nextFromStr(s, with_issuer)
        if iou is not None and rest == '':
            return iou
        return None
    def __eq__(self, other):
        return self.currency == other.currency and self.issuer == other.issuer
    def __ne__(self, other):
        return not (self == other)
    def toStr(self):
        return f'{self.currency}/{self.issuer}'
    def json(self):
        if self.native():
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
        elif j['currency'] == 'XRP':
            return Issue('XRP')
        return Issue(j['currency'], j['issuer'])

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
        else:
            return Amount(Issue(j['currency'], j['issuer']), float(j['value']))
    def fromLineJson(j):
        return Amount(Issue(j['currency'], j['account']), float(j['balance']))
    def fromStr(s, with_issuer = False):
        (amt, rest) = Amount.nextFromStr(s, with_issuer)
        if amt is not None and rest == '':
            return amt
        return None
    def toStr(self):
        return f'{self.value}/{self.issue.currency}'
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
    return re.sub(',\s*}', '\n}', s)

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
        print(do_format(pprint.pformat(json.loads(request))))
    res = requests.post(url, json = json.loads(request))

    if res.status_code != 200:
        raise Exception(res.text)
    j = json.loads(request)
    if 'method' in j and j['method'] == 'submit':
        if j['params'][0]['tx_json']['TransactionType'] == 'AMMCreate':
            time.sleep(6)
        else:
            time.sleep(tx_wait)
    j = json.loads(res.text)
    return j

def quoted(val):
    if type(val) == str and val != 'true' and val != 'false':
        return f'"%s"' % val
    return str(val)

def get_field(field, val, delim=True):
    if val is None:
        return ""
    elif type(val) == Amount or type(val) == Issue:
        return """
        "%s": %s%s
        """ % (field, val.json(), ',' if delim else '')
    elif type(val) == str and re.search(r'false|true', val):
        return """
        "%s": %s%s
        """ % (field, val, ',' if delim else '')
    return """
        "%s": %s%s
    """ % (field, json.JSONEncoder().encode(val), ',' if delim else '')

def get_with_prefix(prefix, params):
    rx = Re()
    r = r'' + prefix + '([^\s]+)'
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
    res = requests.post('https://ammfaucet.devnet.rippletest.net/accounts', json=json.loads(request))
    if res.status_code != 200:
        raise Exception(res.text)
    return json.loads(res.text)

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
                    paths = None,
                    sendMax: Amount = None,
                    fee = "10", flags = "2147483648"):
    def path_(paths):
        if paths is not None:
            return f',"Paths": {json.dumps(paths)}'
        return ""
    def sendmax_(sendMax):
        if sendMax is not None:
            return f',"SendMax": {sendMax.json()}'
        return ""
    return """
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
                "Flags": "%s"
                %s
                %s
            }
        }
    ]
    }
    """ % (secret, account, amount.json(), destination, fee, flags, sendmax_(sendMax), path_(paths))

def wallet_request():
    return """
        {
           "method": "wallet_propose",
           "params": [{}]
        }
        """

def tx_request(hash):
    return """
    {
    "method": "tx",
    "params": [
        {
            "transaction": "%s",
            "binary": false,
            "ledger_index": "validated"
        }
    ]
    }
    """ % hash

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


def amm_swap_request(secret, account, hash, dir, asset: Amount, assetLimit: Amount = None, splimit = None, slippage = None, fee = "10"):
    def fields():
        if dir == 'in':
            return f'"AssetIn": {asset.json()},'
        elif dir == 'out':
            return f'"AssetOut": {asset.json()},'
        elif assetLimit is not None:
            return """
                "Asset": %s,
                "AssetLimit": %s,
            """ % (asset.json(), assetLimit.json())
    return """
    {
    "method": "submit",
    "params": [
        {
             "secret": "%s",
             "tx_json": {
                 "Flags": 0,
                 "Account" : "%s",
                 "AMMID" : "%s",
                 "Fee": "%s",
                 %s
                 "TransactionType" : "AMMSwap"
             }
        }
    ]
    }
    """ % (secret, account, hash, fee, fields())

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
            "account": "%s",
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
    """ % (account, get_field('ledger_hash', hash), get_field('ledger_index', index),
           get_field('limit', limit), get_field('binary', binary), get_field('marker', marker),
           get_field('ledger_index_min', min), get_field('ledger_index_max', max),
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

def ledger_entry_request(asset=None, asset2=None, id=None, index='validated'):
    assets_res = True if asset is not None and asset2 is not None else False
    id_res = True if id is not None else False
    assert (assets_res and not id_res) or (id_res and not assets_res)
    if id is not None:
        return """
        {
          "method": "ledger_entry",
          "params": [
            {
            "amm": "%s",
            "ledger_index": "%s"
            }
          ]
        }
        """ % (id, index)
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
            "ledger_index": "%s"
            }
          ]
        }
        """ % (asset.json(), asset2.json(), index)

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
    if node == ammdevnet:
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
                    return None
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

# pay src dst[,dst1,...] amount currency [sendmax [path1,path2...]]
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
            paths = None
            sendmax = None
            flags = "2147483648"
            if rx.search(r'^\s*([^\s]+)\s+([^\s]+)(.*)?$', rest):
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
                        break
                    if verbose:
                        print(res)
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
    if rx.search(r'^\s*amm\s+create\s+([^\s]+)\s+(([^\s]+)\s+(.+))$', line):
        if getAccountId(rx.match[3]) is not None:
            alias = rx.match[1]
            account = rx.match[3]
            rest = rx.match[4]
        else:
            account = rx.match[1]
            rest = rx.match[2]
            alias = None
        if not getAccountId(account):
            print(account, 'account not found')
            return None
        (amt1, rest) = Amount.nextFromStr(rest)
        if amt1 is None:
            print('invalid amount')
            return None
        (amt2, rest) = Amount.nextFromStr(rest)
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
                alias = f'amm{amt1.issue.currency}-{amt2.issue.currency}'
            else:
                # rm previous alias if exists
                alias_prev = f'amm{amt1.issue.currency}-{amt2.issue.currency}'
                if alias_prev in accounts:
                    del accounts[alias_prev]
            cur = f'{amt1.issue.currency}-{amt2.issue.currency}'
            verbose = False
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
    return c != 'XRP' and c not in issuers

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
# amm info [currency1 currency2] [amm_account] [account] [\[Amount,Amount2...\]] [$ledger] [save key]
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
    if rx.search(r'\s+\$([^\s]+)', line):
        index = rx.match[1]
        line = re.sub(r'\s+\$([^\s]+)', '', line)
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

# swap [in|out] acct ammAcct amount
# swap acct ammAcct asset assetLimit
def amm_swap(line):
    rx = Re()
    if rx.search(r'^\s*amm\s+swap\s+((in|out)\s+)?([^\s]+)\s+([^\s]+)\s+(.+)$', line):
        dir = rx.match[2]
        account = rx.match[3]
        account_id = getAccountId(account)
        if account_id is None:
            print(rx.match[3], 'account not found')
            return False
        hash = getAMMHash(rx.match[4])
        if hash is None:
            print(rx.match[4], 'hash not found')
            return False
        asset,rest = Amount.nextFromStr(rx.match[5])
        if asset == None:
            return False
        assetLimit = None
        if dir is None:
            assetLimit = Amount.fromStr(rest)
        elif not rx.search(r'^\s*$', rest):
            return False
        request = amm_swap_request(accounts[account]['seed'], account_id, hash, dir, asset, assetLimit)
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
            hash, index = get_params(rx.match[2])
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

# show available commands
def help(line):
    rx = Re()
    if rx.search(r'^\s*help\s*$', line):
        print('fund name[,name1,...] <XRP amount>: create account by funding from genesis')
        print('trust set account amount currency issuer: create trust line')
        print('pay src dst[,dst1,...] <amount> currency: pay from source into destinations')
        print('account lines name: print account trust lines')
        print('account info name: get account info')
        print('name: print account id and master seed')
        print(
            'amm create account[(amm alias)] amount1 currency1 amount2 currency2 [trading fee]: create amm for the given tokens')
        print('amm deposit account amm [tokens | amount1 [tokens|amount2]]: deposit into amm instance')
        print('amm withdraw account amm [tokens | amount1 [tokens|amount2]]: withdraw from amm instance')
        print('amm info currency1 currency2 [account]')
        print('session restore : load previously created accounts (accounts might be unfunded')
        print('history [n-[n1]]: print all history or replay history n-n1')
        print('clear history: clear history')
        print('accounts: show all accounts')
        print('issuers: show all currencies/issuers')
        print('offer create account takerGetsAmount takerPaysAmount: create order book offer')
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
        return None
    return False

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
    rx = Re()
    if rx.search(r'^\s*clear\s+all\s*$', line):
        accounts = defaultdict(defaultdict)
        issuers = defaultdict()
        dump_accounts()
        dump_issuers()
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
        return True
    return False

def toggle_verbose(line):
    rx = Re()
    global verbose
    if rx.search(r'^\s*verbose\s+(on|off)\s*$', line):
        verbose = (rx.match[1] == 'on')
        return True
    return False

def set_node(line):
    rx = Re()
    global node
    global port
    if rx.search(r'^\s*set\s+node\s+(http://[^\s]+):(\d+)\s*$', line):
        node = rx.match[1]
        port = int(rx.match[2])
        return True
    elif rx.search(r'^\s*set\s+node\s+([^\s:]+):(\d+)\s*$', line):
        node = rx.match[1]
        if node == 'ammdevnet':
            node = ammdevnet
        port = int(rx.match[2])
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
        return None
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
    if rx.search(r'^\s*tx\s+([^\s]+)\s*$', line):
        txid = rx.match[1]
        request = tx_request(txid)
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

# ledger entry XRP USD | object_id
def ledger_entry(line):
    rx = Re()
    # ledger entry object_id
    if rx.search(r'^\s*ledger\s+entry\s+([^\s]+)\s*$', line):
        id = rx.match[1]
        req = ledger_entry_request(id=id)
        res = send_request(req, node, port)
        print(do_format(pprint.pformat(res)))
        return True
    # ledger entry asset asset2
    elif rx.search(r'^\s*ledger\s+entry\s+([^\s]+)\s+([^\s]+)\s*$', line):
        asset = Issue.fromStr(rx.match[1])
        if asset is None:
            print('Invalid asset', asset)
            return None
        asset2 = Issue.fromStr(rx.match[2])
        if asset2 is None:
            print('Invalid asset', asset2)
            return None
        print('call ledger entry')
        req = ledger_entry_request(asset=asset, asset2=asset2)
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
        req = ledger_data_request(hash=hash, index=index, limit=limit, binary=binary, marker=marker, type_ = type_)
        res = send_request(req, node, port)
        print(do_format(pprint.pformat(res)))
        return True
    return False

# account objects account [#hash] [@index] [$limit] [^maker] [delete_only] [type]
def account_objects(line):
    rx = Re()
    if rx.search(r'^\s*account\s+objects\s+([^\s]+)(.+)\s*$', line):
        account = getAccountId(rx.match[1])
        if account is None:
            print(rx.match[1], 'account not found')
            return None
        rest = rx.match[2]
        type_ = None
        hash, index, rest = get_params(rest, 'validated')
        limit, marker, delete_only, rest = get_params_ext(rest)
        rest = re.sub(r'\s+', '', rest)
        if rest != '':
            type_ = rest
        req = account_objects_request(account, hash=hash, index=index, limit=limit, marker=marker,
                                      delete_only = delete_only, type_ = type_)
        res = send_request(req, node, port)
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

def account_commands(line):
    rx = Re()
    if rx.search(r'^\s*account\s+(objects|info|lines|offers|SetFlag|ClearFlag|delete|channels|currencies|nfts|tx)', line):
        cmd = {'objects': account_objects, 'info': account_info, 'lines': account_lines,
               'offers': account_offers, 'SetFlag': account_set, 'ClearFlag': account_set,
               'delete': account_delete, 'channels': account_channels, 'currencies': account_currencies,
               'nfts': account_nfts, 'tx': account_tx}
        return cmd[rx.match[1]](line)
    if rx.search(r'^\s*gateway\s+balances', line):
        return gateway_balances(line)
    if rx.search(r'^\s*noripple\s+check', line):
        return noripple_check(line)
    return False

def offer_commands(line):
    rx = Re()
    if rx.search(r'^\s*offer\s+(create|cancel)', line):
        cmd = {'create': offer_create, 'cancel': offer_cancel}
        return cmd[rx.match[1]](line)
    return False

def amm_commands(line):
    rx = Re()
    if rx.search(r'^\s*amm\s+(create|deposit|withdraw|swap|vote|bid|info|hash)', line):
        cmd = {'create': amm_create, 'deposit': amm_deposit, 'withdraw': amm_withdraw,
               'swap': amm_swap, 'vote': amm_vote, 'bid': amm_bid, 'info': amm_info,
               'hash': amm_hash}
        return cmd[rx.match[1]](line)
    return False


commands = {
    'account': account_commands,
    'accounts': show_accounts,
    'amm': amm_commands,
    'book': book_offers,
    'clearall': clear_all,
    'clearhistory': clear_history,
    'clearstore': clear_store,
    'expectamm': expect_amm,
    'expectauction': expect_auction,
    'expectbalance': expect_balance,
    'expectfee': expect_trading_fee,
    'expectline': expect_line,
    'expectoffers': expect_offers,
    'flags': flags,
    'fund': fund,
    'getbalance': get_balance,
    'getline': get_line,
    'help': help,
    'history': history,
    'h': history,
    'issuers': show_issuers,
    'last': last,
    'ledgerentry': ledger_entry,
    'ledgerdata': ledger_data,
    'load': load_accounts,
    'offer': offer_commands,
    'path': path_find,
    'pay': pay,
    'pprint': toggle_pprint,
    'repeat': repeat_cmd,
    'run': run_script,
    'session': session_restore,
    'serverinfo': server_info,
    'serverstate': server_state,
    'setaccount': set_account,
    'setissue': set_issue,
    'setnode': set_node,
    'setwait': set_wait,
    'trust': trust_set,
    'tx': tx_lookup,
    'verbose': toggle_verbose,
    'wait': wait
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
                res = commands[k1](line)
            elif k2 is None:
                res = print_account(line)
            elif k2 in commands:
                res = commands[k2](line)
        except Exception as ex:
            print(line, ex)
            res = None

    if res is not None and res:
        history.append(line.strip('\n'))
        with open('history.json', 'w') as f:
            json.dump(history, f)
    elif res is not None:
        print('invalid command')

if __name__ == "__main__":
    if script is not None:
        exec_command(f'run {script}')
    else:
        prompt()
        for line in sys.stdin:
            exec_command(line)
            prompt()

