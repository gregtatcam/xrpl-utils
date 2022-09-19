#!/usr/bin/env python3
import requests
import sys
import random
import json
import time
import re
import pprint
from collections import defaultdict

drops_per_xrp = 1_000_000
port = 5005
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
# currency to the issuer map
issuers = defaultdict()
# commands history
history = list()
# print each outgoing request if true
verbose = False

validFlags = {'noDirectRipple':65536, 'partialPayment':131072, 'limitQuality':262144,
             'passive':65536, 'immediateOrCancel':131072, 'fillOrKill': 262144,
             'sell': 524288, 'accountTxnID':5, 'authorizedNFTokenMinter': 10, 'defaultRipple': 8,
             'depositAuth': 9, 'disableMaster': 4, 'disallowXRP': 3, 'globalFreeze': 7, 'noFreeze': 6,
             'requireAuth': 2, 'requireDest': 1, 'withdrawAll': 65536, 'noRippleDirect': 65536}

try:
    with open('history.json', 'r') as f:
        history = json.load(f)
except:
    pass

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
                ps.append({"currency": cur, "issuer": getAccountId(issuers[cur])})
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


class Issue:
    def __init__(self, currency, issuer = None):
        # hack to pass in drops
        self.drops = False
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
            hash = getAMMHash(rx.match[1])
            if hash is None:
                print(rx.match[1], 'hash not found')
                return (None, s)
            return (getAMMIssue(rx.match[1]), rx.match[2])
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
            """ % (self.issue.currency, self.issue.issuer, self.value)
    def fromIssue(iou: Issue):
        return Amount(iou, 0)
    # <amount XRP|IOU
    def nextFromStr(s, with_issuer = False):
        rx = Re()
        if not rx.search(r'^\s*(\d+(\.\d+)?)\s*(.+)$', s):
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

def send_request(request, node = None, port = '5005'):
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
        if j['params'][0]['tx_json']['TransactionType'] == 'AMMInstanceCreate':
            time.sleep(6)
        else:
            time.sleep(tx_wait)
    j = json.loads(res.text)
    return j

def faucet_send_request(request):
    res = requests.post('https://ammfaucet.devnet.rippletest.net/account', json=json.loads(request))
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

def account_info_request(account):
    return """
    {
    "method": "account_info",
    "params": [
        {
            "account": "%s",
            "ledger_index": "validated"
        }
    ]
    }
    """ % (account)

# issuer - the address of the account to extend trust to
# value - limit of the trust
def trust_request(secret, account, amount: Amount, fee = '10'):
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
                "Flags": 262144,
                "LimitAmount": %s
            }
        }
    ]
    }
    """ % (secret, account, fee, amount.json())

def account_trust_lines_request(account):
    return """
    {
    "method": "account_lines",
    "params": [
        {
            "account": "%s",
            "ledger_index": "validated"
        }
    ]
    }
    """ % (account)

def amm_create_request(secret, account, asset1: Amount, asset2: Amount, tradingFee="0", fee="10"):
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
                "Asset1" : %s,
                "Asset2" : %s,
                "TransactionType" : "AMMInstanceCreate"
            }
       }
   ]
   }
   """ % (secret, account, fee, tradingFee, asset1.json(), asset2.json())

def amm_info_request(account, iou1: Issue, iou2: Issue, ledger_index = "validated"):
    def acct(account):
        if account is not None:
            return f'"account": "{account}",'
        else:
            return ""
    def index():
        if ledger_index != None:
            return f',\n"ledger_index": "{ledger_index}"'
        else:
            return ""
    return """ {
   "method": "amm_info",
   "params": [
       {
           %s
           "asset1" : %s,
           "asset2" : %s
           %s
       }
   ]
   }
   """ % (acct(account), Amount.fromIssue(iou1).json(), Amount.fromIssue(iou2).json(), index())

def amm_info_by_hash_request(account, hash, ledger_index = "validated"):
    def acct(account):
        if account is not None:
            return f'"account": "{account}",'
        else:
            return ""

    def index():
        if ledger_index != None:
            return f',\n"ledger_index": "{ledger_index}"'
        else:
            return ""

    return """ {
    "method": "amm_info",
    "params": [
        {
            %s
            "amm_id" : "%s"
            %s
        }
    ]
    }
    """ % (acct(account), hash, index())

'''
LPToken
Asset1In
Asset1In and Asset2In
Asset1In and LPToken
Asset1In and EPrice
'''
def amm_deposit_request(secret, account, hash, tokens: Amount = None, asset1: Amount = None, asset2: Amount = None, eprice: Amount = None, fee="10"):
    def fields():
        if asset1 is not None:
            if asset2 is not None:
                return """
                    "Asset1In": %s,
                    "Asset2In": %s,
                """ % (asset1.json(), asset2.json())
            if tokens is not None:
                return """
                "Asset1In": %s,
                "LPToken": %s,
            """ % (asset1.json(), tokens.json())
            elif eprice is not None:
                return """
                "Asset1In": %s,
                "EPrice": %s,
                """ % (asset1.json(), eprice.json())
            else:
                return f'"Asset1In": {asset1.json()},'
        elif tokens is not None:
            return f'"LPToken": {tokens.json()},'
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
                 "TransactionType" : "AMMDeposit"
             }
        }
    ]
    }
    """ % (secret, account, hash, fee, fields())

'''
LPToken
Asset1Out
Asset1Out and Asset2Out
Asset1Out and LPToken
Asset1Out and EPrice
'''
def amm_withdraw_request(secret, account, hash, tokens: Amount = None, asset1: Amount = None, asset2: Amount = None, eprice: Amount=None, fee="10"):
    flags = 0
    def fields():
        nonlocal flags
        if asset1 is not None:
            if asset2 is not None:
                return """
                    "Asset1Out": %s,
                    "Asset2Out": %s,
                """ % (asset1.json(), asset2.json())
            if tokens is not None:
                if tokens.value == 0.0:
                    flags = 65536
                    return """
                        "Asset1Out": %s,
                        """ % (asset1.json())
                return """
                    "Asset1Out": %s,
                    "LPToken": %s,
                    """ % (asset1.json(), tokens.json())
            if eprice is not None:
                return """
                "Asset1Out": %s,
                "EPrice": %s,
                """ % (asset1.json(), eprice.json())
            else:
                return f'"Asset1Out": {asset1.json()},'
        elif tokens is not None:
            if tokens.value == 0.0:
                flags = 65536
                return ""
            return f'"LPToken": {tokens.json()},'
    return """
    {
    "method": "submit",
    "params": [
        {
             "secret": "%s",
             "tx_json": {
                 "Account" : "%s",
                 "AMMID" : "%s",
                 "Fee": "%s",
                 %s
                 "TransactionType" : "AMMWithdraw",
                 "Flags": %d
             }
        }
    ]
    }
    """ % (secret, account, hash, fee, fields(), flags)


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

def account_offers_request(account):
    return """
    {
    "method": "account_offers",
    "params": [
        {
            "account": "%s"
        }
    ]
    }
    """ % (account)

def vote_request(secret: str, account: str, hash: str,
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
                "AMMID": "%s",
                "FeeVal": %s,
                "Fee": "%s",
                "Flags": %d
            }
        }
    ]
    }
    """ % (secret, account, hash, feeVal, fee, flags)

def bid_request(secret: str, account: str, hash: str,
                 pricet: str, bid: Amount, authAccounts = None, flags=0, fee="10"):
    if pricet == 'min':
        pricet = 'MinSlotPrice'
    else:
        pricet = 'MaxSlotPrice'
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
            s += '{"AuthAccount": {"Account": "' + account_id + '"}}' + d
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
                "AMMID": "%s",
                %s
                %s
                "Fee": "%s",
                "Flags": %d
            }
        }
    ]
    }
    """ % (secret, account, hash, get_bid(), get_accounts(), fee, flags)


def do_format(s):
    return s
    s = re.sub(genesis_acct, 'genesis', s)
    for i in range(len(accounts)-1):
        s = re.sub(accounts[i]['id'], 'acct%d' % i, s)
    return s

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

# fund account[,account1,...] XRP: fund via ammdevnet faucet
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
        with open('accounts.json', 'w') as f:
            json.dump(accounts, f)
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
        with open('accounts.json', 'w') as f:
            json.dump(accounts, f)
        return True
    return False

# trust set account,account1,.. amount currency issuer
def trust_set(line):
    rx = Re()
    global accounts
    if rx.search(r'^\s*trust\s+set\s+([^\s]+)\s+(.+)$', line):
        accts = rx.match[1]
        rest = rx.match[2]
        amount = Amount.fromStr(rest, True)
        if amount is None:
            print('invalid amount')
            return None
        save_wait()
        try:
            for account in accts.split(','):
                if not account in accounts:
                    print(account, 'account not found')
                else:
                    request = trust_request(accounts[account]['seed'],
                                            accounts[account]['id'],
                                            amount)
                    res = send_request(request, node, port)
                    if not error(res):
                        issuers[amount.issue.currency] = getAlias(amount.issue.issuer)
        except Exception as ex:
            raise ex
        finally:
            restore_wait()
        with open('issuers.json', 'w') as f:
            json.dump(issuers, f)
        return True
    return False

# account info account
def account_info(line):
    rx = Re()
    global accounts
    if rx.search(r'^\s*account\s+info\s+([^\s]+)\s*$', line):
        for account in rx.match[1].split(','):
            id = getAccountId(account)
            if id is not None:
                request = account_info_request(id)
                res = send_request(request, node, port)
                if 'account_data' in res['result']:
                    print(do_format(pprint.pformat(res['result']['account_data'])))
                elif 'error_message' in res['result']:
                    print(do_format(pprint.pformat(res['result']['error_message'])))
                else:
                    print(do_format(pprint.pformat(res['result'])))
            else:
                print(account, 'account not found')
        return True
    return False

# account lines account
def account_lines(line):
    global accounts
    rx = Re()
    if rx.search(r'^\s*account\s+lines\s+([^\s]+)\s*$', line):
        for account in rx.match[1].split(','):
            id = getAccountId(account)
            if id is not None:
                request = account_trust_lines_request(id)
                res = send_request(request, node, port)
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
            except Exception as ex:
                raise ex
            finally:
                restore_wait()
        return True
    return False

# amm create account [$alias] amount currency amount currency [trading fee]
def amm_create(line):
    global accounts
    global verbose
    rx = Re()
    if rx.search(r'^\s*amm\s+create(\s+@([^\s]+))?\s+([^\s]+)\s+(.+)$', line):
        account = rx.match[3]
        rest = rx.match[4]
        alias = None
        if rx.match[1] is not None:
            alias = rx.match[2]
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
            verboseSave = verbose
            request = amm_create_request(accounts[account]['seed'],
                                         accounts[account]['id'],
                                         amt1,
                                         amt2,
                                         tfee)
            res = send_request(request, node, port)
            error(res)
            if alias is None:
                alias = f'amm{amt1.issue.currency}-{amt2.issue.currency}'
            verbose = False
            request = amm_info_request(None, amt1.issue, amt2.issue, 'validated')
            res = send_request(request, node, port)
            verbose = verboseSave
            if 'result' not in res:
                print('amm create failed')
            else:
                result = res['result']
                ammAccount = result['AMMAccount']
                tokens = result['LPToken']
                accounts[alias] = {'id': ammAccount,
                                   'hash': result['AMMID'],
                                   'issue': {'currency': tokens['currency'], 'issuer': tokens['issuer']}}
                with open('accounts.json', 'w') as f:
                    json.dump(accounts, f)
        return True
    return False

def not_currency(c):
    return c != 'XRP' and c not in issuers

# s is either a hash or amm alias
def getAMMHash(s):
    if s in accounts:
        return accounts[s]['hash']
    return s

def getAMMIssue(s) -> Issue :
    if s in accounts:
        issue = accounts[s]['issue']
        return Issue(issue['currency'], issue['issuer'])
    for a,v in accounts.items():
        if 'hash' in v and v['hash'] == s:
            issue = v['issue']
            return Issue(issue['currency'], issue['issuer'])
    return None

# amm info currency1 currency2 [account]
# amm info hash [account]
def amm_info(line):
    global accounts
    def do_save(res, alias):
        result = res['result']
        tokens = result['LPToken']
        accounts[alias] = {'id': result['AMMAccount'],
                           'hash': result['AMMID'],
                           'issue': {'currency': tokens['currency'], 'issuer': tokens['issuer']}}
        with open('accounts.json', 'w') as f:
            json.dump(accounts, f)
    rx = Re()
    # save?
    save = None
    if rx.search(r'\s+save\s+([^\s]+)\s*$', line):
        save = rx.match[1]
        line = re.sub(r'\s+save\s+([^\s]+)\s*$', '', line)
    if rx.search(r'^\s*amm\s+info\s+(.+)$', line):
        rest = rx.match[1]
        if rx.search(r'^\s*([^\s]+)(\s+([^\s]+))?\s*$', rest) and not_currency(rx.match[1]):
            # either an amm alias or amm hash
            hash = getAMMHash(rx.match[1])
            if hash is None:
                return False
            account = rx.match[3]
            request = amm_info_by_hash_request(getAccountId(account), hash, 'validated')
            res = send_request(request, node, port)
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
                                       'validated')
            res = send_request(request, node, port)
            print(do_format(pprint.pformat(res['result'])))
            if save is not None:
                do_save(res, save)
        return True
    return False

# amm deposit account hash tokens
# amm deposit account hash asset1in
# amm deposit account hash asset1in asset2in
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
        hash = getAMMHash(rx.match[2])
        if hash is None:
            print(rx.match[2], 'hash not found')
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
                elif rx.search(r'^\s*@([^\s]+)\s*$', rest):
                    eprice, rest = Amount.nextFromStr(rx.match[1])
                    if eprice is None or rest != '':
                        return False
                # amount
                else:
                    print('got amount')
                    asset2, rest = Amount.nextFromStr(rest)
                    if asset2 is None or rest != '':
                        return False
        request = amm_deposit_request(accounts[account]['seed'], account_id, hash, tokens, asset1, asset2, eprice)
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
    if rx.search(r'^\s*amm\s+withdraw\s+([^\s]+)\s+([^\s]+)\s+(.+)$', line):
        account = rx.match[1]
        account_id = getAccountId(rx.match[1])
        if account_id is None:
            print(rx.match[1], 'account not found')
            return None
        hash = getAMMHash(rx.match[2])
        if hash is None:
            print(rx.match[2], 'hash not found')
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
                    eprice = Amount(getAMMIssue(hash), float(rx.match[1]))
                # amount
                else:
                    asset2, rest = Amount.nextFromStr(rest)
                    if asset2 is None or rest != '':
                        return False
        request = amm_withdraw_request(accounts[account]['seed'], account_id, hash, tokens, asset1, asset2, eprice)
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

# offer create acct takerPaysAmt takerGetsAmt
def offer(line):
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
            return False
        takerGets,rest = Amount.nextFromStr(rest)
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

def account_offers(line):
    rx = Re()
    if rx.search(r'^\s*account\s+offers\s+([^\s]+)$', line):
        for account in rx.match[1].split(','):
            id = getAccountId(account)
            if id is None:
                print(account, 'account not found')
                return None
            request = account_offers_request(id)
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
            with open('accounts.json', 'r') as f:
                accounts = json.load(f)
            with open('issuers.json', 'r') as f:
                issuers = json.load(f)
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
        hash = getAMMHash(rx.match[2])
        if hash is None:
            print(rx.match[2], 'hash not found')
            return False
        feeVal = int(rx.match[3])
        request = vote_request(accounts[account]['seed'], account_id, hash, feeVal)
        res = send_request(request, node, port)
        error(res)
        return True
    return False

# amm bid lp hash (min|max) price
def amm_bid(line):
    rx = Re()
    if rx.search(r'\s*amm\s+bid\s+([^\s]+)\s+([^\s]+)\s+(min|max)\s+([^\s]+)(\s+([^\s+]+))?\s*$', line):
        account = rx.match[1]
        account_id = getAccountId(account)
        if account_id is None:
            print(account, 'account not found')
            return False
        hash = getAMMHash(rx.match[2])
        if hash is None:
            print(rx.match[2], 'hash not found')
            return False
        pricet = rx.match[3]
        issue = getAMMIssue(rx.match[2])
        bid = Amount(issue, float(rx.match[4]))
        authAccounts = None
        if rx.match[6] is not None:
            authAccounts = rx.match[6].split(',')
        request = bid_request(accounts[account]['seed'], account_id, hash, pricet, bid, authAccounts)
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
        request = amm_info_request(None, amToken1.issue, amToken2.issue, 'validated')
        res = send_request(request, node, port)
        if 'error' in res['result']:
            raise Exception(f'{line.rstrip()}: {res["result"]["error"]}')
        asset1 = Amount.fromJson(res['result']['Asset1'])
        asset2 = Amount.fromJson(res['result']['Asset2'])
        token = res['result']['LPToken']['value']

        def ne(asset: Amount) -> bool :
            if asset.issue == asset1.issue:
                return asset.value != asset1.value
            if asset.issue == asset2.issue:
                return asset.value != asset2.value
            return True

        if ne(amToken1) or ne(amToken2) or (lpToken is not None and lpToken != token):
            raise Exception(f'{line.strip()}: ##FAILED## {amToken1.toStr()},{asset1.toStr()},'
                            f'{amToken2.toStr()},{asset2.toStr()}')

    rx = Re()
    if rx.search(r'\s*expect\s+amm\s+([^\s]+)\s+none\s*$', line):
        hash = getAMMHash(rx.match[1])
        if hash == rx.match[1]:
            print(rx.match[1], 'hash not found')
            return None
        request = amm_info_by_hash_request(None, hash)
        res = send_request(request, node, port)
        if res['result']['error'] != 'actNotFound':
            raise Exception(f'{line.rstrip()}: ##FAILED## {res}')
        return True
    # expect amm hash account lptoken | expect amm token1 token2 lptoken?
    elif rx.search(r'\s*expect\s+amm\s+([^\s]+)\s+([^\s]+)\s+(\d+(\.\d+)?)?\s*$', line):
        if isAddress(rx.match[2]):
            hash = getAMMHash(rx.match[1])
            if hash == rx.match[1]:
                print(rx.match[1], 'hash not found')
                return None
            account = rx.match[2]
            account_id = getAccountId(account)
            if account_id is None:
                print(account, 'account not found')
                return None
            tokens = rx.match[3]
            if tokens is None:
                print('tokens must be specified')
                return None
            request = amm_info_by_hash_request(account_id, hash)
            res = send_request(request, node, port)
            if tokens != res['result']['LPToken']['value']:
                raise Exception(f'{line.rstrip()}: ##FAILED## {res["result"]["LPToken"]["value"]}')
        else:
            proc(rx.match[1], rx.match[2], rx.match[4])
        return True
    return False

# expect fee hash fee
def expect_trading_fee(line):
    rx = Re()
    if rx.search(r'^\s*expect\s+fee\s+([^\s]+)\s+(\d+)\s*$', line):
        hash = getAMMHash(rx.match[1])
        if hash == rx.match[1]:
            print(rx.match[1], 'hash not found')
            return None
        fee = rx.match[2]
        request = amm_info_by_hash_request(None, hash)
        res = send_request(request, node, port)
        if res['result']['TradingFee'] != int(fee):
            raise Exception(f'{line.rstrip()}: ##FAILED## {res["result"]["TradingFee"]}')
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
def repeat_cmd(line):
    rx = Re()
    if rx.search(r'^\s*repeat\s+(\d+)\s+(\d+)\s+(.+)$', line):
        start = int(rx.match[1])
        end = int(rx.match[2])
        cmd_ = rx.match[3]
        cmd__ = cmd_
        for i in range(start, end + 1):
            if rx.search(r'\((\d+)\s*\*\s*\$i\)', cmd_):
                expr = int(rx.match[1]) * i
                cmd__ = re.sub(r'\((.+)\)', str(expr), cmd_)
            cmd = re.sub(r'\$i', str(i), cmd__)
            exec_command(cmd)
        return True
    return False

# expect auction hash fee timeInterval price
def expect_auction(line):
    rx = Re()
    if rx.search(r'^\s*expect\s+auction\s+([^\s]+)\s+(\d+)\s+(\d+)\s+([^\s]+)\s*$', line):
        hash = getAMMHash(rx.match[1])
        if hash == rx.match[1]:
            print(rx.match[1], 'hash not found')
            return None
        fee = int(rx.match[2])
        interval = int(rx.match[3])
        price = rx.match[4]
        request = amm_info_by_hash_request(None, hash)
        res = send_request(request, node, port)
        slot = res['result']['AuctionSlot']
        efee = slot['DiscountedFee']
        eprice = slot['Price']['value']
        einterval = slot['TimeInterval']
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

commands = [repeat_cmd, fund, faucet_fund, trust_set, account_info, account_lines, pay, amm_create,
            amm_deposit, amm_withdraw, amm_swap, amm_info, session_restore,
            help, do_history, clear_history, show_accounts, print_account,
            show_issuers, toggle_verbose, last, offer, account_offers, set_node,
            set_account, set_issue, offer_cancel, account_set, flags, load_accounts,
            server_info, amm_vote, amm_bid, amm_hash, expect_amm, expect_line,
            expect_offers, expect_balance, wait, run_script, expect_trading_fee,
            expect_auction, get_line, get_balance, set_wait]

def prompt():
    sys.stdout.write('> ')
    sys.stdout.flush()

def exec_command(line):
    rx = Re()
    if rx.search(r'^\s*$', line):
        return
    global commands
    res = None
    for command in commands:
        try:
            res = command(line)
        except Exception as ex:
            print(ex)
            res = None
            break
        if res is None or res:
            break
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

