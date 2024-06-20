#!/usr/bin/env python3
import pprint
import requests
import sys
import json
import re
from collections import defaultdict

port = 51234
node = '127.0.0.1'
script = None
drops_per_xrp = 1_000_000
do_pprint = True
meta_filter = \
    '[LedgerEntryType:[Offer,RippleState,AccountRoot],' \
    'TakerPays,TakerGets,Account,Balance,Flags,HighLimit,LowLimit]'

i = 1
while i < len(sys.argv):
    if sys.argv[i] == '--node':
        i += 1
        node = sys.argv[i]
    elif sys.argv[i] == '--port':
        i += 1
        port = int(sys.argv[i])
    elif sys.argv[i] == '--file':
        i += 1
        script = sys.argv[i]
    i += 1

if script is None:
    print('script must be provided')
    exit(0)

"""
script syntax:
fund a1[,a2,...] 1000XRP: create accounts and fund with 1000XRP (could be any amount)
trust set a2[,a3,...] 1000USD a1: set trustline between the accounts and a1 for 1000USD 
  (could be any amount and IOU)
pay a1 a2[,a3,...] 1000USD: pay 1000USD from a1 (issuer in this case) to the accounts
offer create a1 100USD 100XRP [flags]: create offer by a1 to buy 100USD and sell 100XRP
amm create a1 1000USD 1000XRP: create amm by a1 for 1000USD/1000XRP
pay a1 a2 10USD [USD] 10XRP [flags]: cross currency payment of 10USD from a1 to a2
  and sendMax 10XRP
Note that XRP is not the drops; i.e. 100XRP is going to be parsed into 100,000,000 drops.
Use XRPD to provide drops; i.e. 100,000,000XRPD
"""

genesis_acct = 'rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh'
genesis_sec = 'snoPBrXtMeMyMHUVTgbuqAfg1SUTb'
# accounts keyed by alias, store account_id and master_seed as a pair
accounts = defaultdict(defaultdict)
# currency to the issuer map, can have more than 3 letters/digits, like USD1.
# this allows same currency but different issuer.
# only first three letters are used in the payload.
issuers = defaultdict()

class Re:
    def __init__(self):
        self.match = None
    def search(self, rx, s):
        self.match = re.search(rx, s)
        return self.match is not None

# send request to rippled
def send_request(request, node, port) -> json:
    url = f'http://{node}:{port}'
    j_request = json.loads(request)
    res = requests.post(url, json = j_request)

    if res.status_code != 200:
        raise Exception(res.text)
    if 'method' in j_request and j_request['method'] == 'submit':
        send_request('{"method": "ledger_accept"}', node, port)
    return json.loads(res.text)

def error(res: json) -> bool:
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

def get_issue(cur: str) -> list:
    assert cur in issuers
    return cur[0:3], issuers[cur]

def get_cur_from_amount(amt) -> str:
    if type(amt) == dict:
        return amt['currency']
    else:
        return 'XRP'

def amount_json(val: str, cur: str = 'XRP', issuer: str = None):
    # XRP
    if cur == 'XRP':
        return f'"{int(val) * drops_per_xrp}"'
    if cur == 'XRPD':
        return f'"{val}"'
    if issuer is None:
        cur, issuer = get_issue(cur)
    else:
        cur = cur[0:3]
    return """
    {
        "currency" : "%s",
        "issuer": "%s",
        "value": "%s"
    }
    """ % (cur, issuer, val)

# 1000[.99]USD [gw]
def parse_amount(line: str, with_issuer: bool = False):
    rx = Re()
    if rx.search(r'^\s*(\d+(.\d+)?)([^\s]+)(.*)$', line):
        val = rx.match[1]
        cur = rx.match[3]
        rest = rx.match[4]
        issuer = None
        if with_issuer and rx.search(r'\s+([^\s]+)(.+)$', rest):
            if rx.match[1] in accounts:
                issuer = accounts[rx.match[1]]
                rest = rx.match[2]
        return amount_json(val, cur, issuer), rest
    return None, line

# [[XRP,USD],[GBP,USD]]
def parse_paths(paths:str):
    global issuers
    pathsa = []
    if paths is None:
        return None
    for path in paths.split('],['):
        ps = []
        for cur in path.strip('[]').split(','):
            if cur == 'XRP':
                ps.append({"currency": cur, "issuer": "rrrrrrrrrrrrrrrrrrrrrhoLvTp"})
            elif cur in issuers:
                currency, issuer = get_issue(cur)
                ps.append({"currency": currency, "issuer": issuer})
            else:
                return []
        pathsa.append(ps)
    return pathsa

def get_field(field, val, delim=True, asis=False, num=False, rev_delim=False):
    t = type(val)
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
    elif type(val) == json:
        ret = """
        "%s": %s%s
        """ % (field, val, d)
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


def get_tx_hash(j):
    if 'result' in j and 'tx_json' in j['result'] and 'hash' in j['result']['tx_json']:
        return j['result']['tx_json']['hash']
    return None

#[LedgerEntryType:[RippleState;Offer],Account,...]
def make_objects_filter(str):
    rx = Re()
    filter = None
    if str is not None and rx.search(r'\[([^\s]+)\]', str):
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

def do_format(s):
    global accounts
    if not do_pprint:
        return s
    s = re.sub(genesis_acct, 'genesis', s)
    for (acct, d) in accounts.items():
        s = re.sub(d['id'], acct, s)
        if 'issue' in d:
            s = re.sub(d['issue']['currency'], acct, s)
    return s

#######################
# Requests
#######################

def payment_request(secret: str,
                    account: str,
                    destination: str,
                    amount: json,
                    paths: list,
                    sendMax: json,
                    fee: str = "10",
                    flags: str = "2147483648") -> json:
    paths = None if paths is None else json.dumps(paths)
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
    """ % (secret, account, amount, destination, fee, flags,
           get_field('SendMax', sendMax, asis=True, rev_delim=True),
           get_field('Paths', paths, asis=True, rev_delim=True))

def accountset_request(secret: str, account: str, t: str, flags: str, fee="10") -> str:
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

def trust_request(secret, account, amount: json, flags=262144, fee = '10'):
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
    """ % (secret, account, fee, flags, amount)


def offer_request(secret, account, takerPays: json, takerGets: json, flags=0, fee="10"):
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
    """ % (secret, account, fee, flags, takerPays, takerGets)

def amm_create_request(secret:str, account:str, asset1: json, asset2: json,
                       tradingFee:str ="1", fee:str ="10"):
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
   """ % (secret, account, fee, tradingFee, asset1, asset2)


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
    """ % (hash,
           get_field('ledger_index', index, rev_delim=True),
           get_field('ledger_hash', lhash, rev_delim=True))


#######################
# Commands
#######################

def fund(acct_names: list, xrp_amt: json):
    global accounts
    for name in acct_names:
        res = send_request('{"method": "wallet_propose"}', node, port)
        id = res['result']['account_id']
        seed = res['result']['master_seed']
        accounts[name] = {'id': id, 'seed': seed}
        payment = payment_request(genesis_sec,
                                  genesis_acct,
                                  id,
                                  xrp_amt,
                                  None, # paths
                                  None, # sendMax
                                  flags='0')
        res = send_request(payment, node, port)
        if error(res):
            return
        #print(pprint.pformat(res))
        # Set default ripple
        set = accountset_request(seed, id, 'SetFlag', "8")
        send_request(set, node, port)

# fund a1[,a2...] 1000XRP
def fund_cmd(line:str):
    rx = Re()
    if rx.search(r'^\s*fund\s+([^\s]+)\s+(\d+)(XRP(D)?)\s*$', line):
        acct_names = rx.match[1].split(',')
        amt = rx.match[2]
        xrp = rx.match[3]
        fund(acct_names, amount_json(amt, xrp))
        return True
    return False

def trust_set(issuer: str, acct_names: list, val: str, cur: str, flags: int = 262144):
    global accounts
    global issuers
    for name in acct_names:
        if not name in accounts:
            print(name, 'account not found')
            return
        else:
            request = trust_request(accounts[name]['seed'],
                                    accounts[name]['id'],
                                    amount_json(val, cur, issuer),
                                    flags)
            res = send_request(request, node, port)
            if not error(res):
                issuers[cur] = issuer
                #print(pprint.pformat(res))

# trust set a1[,a2...] 1000USD issuer [flags]
def trust_set_cmd(line: str):
    rx = Re()
    if rx.search(r'^\s*trust\s+set\s+([^\s]+)\s+(\d+(.\d+))([^\s]+)\s+([^\s]+)(\s+(\d+))?$', line):
        acct_names = rx.match[1].split(',')
        val = rx.match[2]
        cur = rx.match[4]
        issuer = rx.match[5]
        flags = int(rx.match[7]) if rx.match[7] is not None else 262144
        if issuer not in accounts:
            print(issuer, 'not found')
            return False
        trust_set(accounts[issuer]['id'], acct_names, val, cur, flags)
        return True
    return False

def offer_create(acct: str, takerPays: json, takerGets: json, flags: int = 0):
    request = offer_request(accounts[acct]['seed'],
                            accounts[acct]['id'], takerPays, takerGets, flags=int(flags))
    res = send_request(request, node, port)
    #print(pprint.pformat(res))
    error(res)

# offer create acct takerPaysAmt takerGetsAmt
def offer_create_cmd(line):
    rx = Re()
    if rx.search(r'^\s*offer\s+create\s+([^\s]+)\s+(.+)$', line):
        acct = rx.match[1]
        if acct not in accounts:
            print(acct, 'not found')
            return False
        rest = rx.match[2]
        takerPays, rest = parse_amount(rest, True)
        if takerPays is None:
            print('invalid takerPays')
            return False
        takerGets, rest = parse_amount(rest, True)
        if takerPays is None:
            print('invalid takerGets')
            return False
        flags = int(rx.match[1]) if rx.search(r'\s*(\d+)\s*$', rest) else 0
        offer_create(acct, takerPays, takerGets, flags)
        return True
    return False


def amm_create(acct:str, amount1: json, amount2: json, trading_fee:int):
    global accounts
    res = send_request('{"method":"server_state"}', node, port)
    fee = res['result']['state']['validated_ledger']['reserve_inc']
    request = amm_create_request(accounts[acct]['seed'],
                                 accounts[acct]['id'],
                                 amount1,
                                 amount2,
                                 trading_fee,
                                 fee)
    res = send_request(request, node, port)
    #print(pprint.pformat(res))
    error(res)
    hash = get_tx_hash(res)
    res = tx_lookup(hash, 'validated', filter='[LedgerEntryType:[AMM],Account]')
    acct = res['result']['meta']['AffectedNodes'][0]['CreatedNode']['NewFields']['Account']
    accounts[f'amm{get_cur_from_amount(json.loads(amount1))}-{get_cur_from_amount(json.loads(amount2))}']['id'] = acct


# amm create account amount1 [gw] amount2 [gw] [trading fee]
def amm_create_cmd(line:str):
    rx = Re()
    if rx.search(r'\s*amm\s+create\s+([^\s]+)(.+)$', line):
        acct = rx.match[1]
        rest = rx.match[2]
        if acct not in accounts:
            print(acct, 'not found')
            return False
        amount1, rest = parse_amount(rest, True)
        if amount1 is None:
            print('invalid amount1')
            return False
        amount2, rest = parse_amount(rest, True)
        if amount2 is None:
            print('invalid amount1')
            return False
        trading_fee = int(rx.match[1]) if rx.search(r'\s*(\d+)\s*$', rest) else 0
        amm_create(acct, amount1, amount2, trading_fee)
        return True
    return False

def tx_lookup(hash: str, ledger_index: str = None, ledger_hash: str = None, filter: str = None):
    rx = Re()
    request = tx_request(hash, ledger_index, ledger_hash)
    res = send_request(request, node, port)
    # check if a filter is included to print specified ledger entries
    filter, rest = make_objects_filter(filter)
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
            try:
                if 'Account' in filter:
                    meta = sorted(meta, key=sort_helper)
            except:
                pass
            res['result']['meta']['AffectedNodes'] = meta
        except Exception as ex:
            pass
    return res

def pay(src: str, dst: str, amount: json, send_max: json, paths: json, flags: int):
    payment = payment_request(accounts[src]['seed'],
                              accounts[src]['id'],
                              accounts[dst]['id'],
                              amount,
                              paths,
                              send_max,
                              flags=flags)
    #print(pprint.pformat(payment))
    res = send_request(payment, node, port)
    if error(res):
        return
    return get_tx_hash(res)

# pay src dst[,dst1,...] amount [[path1,path2...] sendmax flags]
def pay_cmd(line:str):
    rx = Re()
    if rx.search(r'^\s*pay\s+([^\s]+)\s+([^\s]+)\s+(.+)$', line):
        src = rx.match[1]
        if src not in accounts:
            print(src, 'not found')
            return False
        acct_names = rx.match[2].split(',')
        amount, rest = parse_amount(rx.match[3])
        send_max = None
        paths = None
        flags = 0
        filter = None
        get_meta = False
        if rx.search(r'with-meta', rest):
            get_meta = True
            filter = meta_filter
            rest = re.sub(r'\s*with-meta\s*', '', rest)
        if rx.search(r'^\s*\[(.+)\](.*)$', rest):
            paths = parse_paths(rx.match[1])
            rest = rx.match[2]
            send_max, rest = parse_amount(rest)
            flags = int(rx.match[1]) if rx.search(r'(\d+)', rest) else 0
        for dst in acct_names:
            if dst not in accounts:
                print(dst, 'not found')
                return False
            hash = pay(src, dst, amount, send_max, paths, flags)
            if get_meta:
                print('----------------------------------------')
                res = tx_lookup(hash, 'validated', filter=filter)
                print(do_format(pprint.pformat(res)))
        return True
    return False

commands = {'fund': fund_cmd,
            'trust': trust_set_cmd,
            'pay': pay_cmd,
            'offer': offer_create_cmd,
            'amm': amm_create_cmd}
rx = Re()
with open(script, 'r') as f:
    lines = f.readlines()
    for line in lines:
        l = line.strip()
        if rx.search(r'^\s*#', l):
            continue
        if rx.search(r'^\s*([^\s]+)', l):
            cmd = rx.match[1]
            if cmd in commands:
                if not commands[cmd](l):
                    print('failed', l)
                    exit(0)
            else:
                print('invalid command', l)
                exit(0)