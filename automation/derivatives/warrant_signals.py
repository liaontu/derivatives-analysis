"""Shared classification; thresholds and issuer exclusions have one definition."""
from collections import defaultdict
try:
    from .warrant_issuers import customer_eligible, POLICY
    from .warrant_focus import numeric
    from .warrant_spreads import positive
except ImportError:
    from warrant_issuers import customer_eligible, POLICY
    from warrant_focus import numeric
    from warrant_spreads import positive
SIGNAL_RULES = {'version':'warrant-relationship-v2','branchStreak':3,'rollSellDays':30,'rollBuyDays':60,'issuerPolicy':POLICY}

def classify_hints(branches, spot_pct):
    """Strategy clues from ranked branches; the issuers' own market-making branches never count as clues."""
    hints, groups = [], defaultdict(list)
    for r in branches:
        if not customer_eligible(r.get('relation')):
            continue
        groups[r['branchCode']].append(r)
        if r['net'] > 0 and r['streak'] >= SIGNAL_RULES['branchStreak'] and numeric(spot_pct) and spot_pct != 0:
            kind = ('call-' if r['cp']=='C' else 'put-') + ('up' if spot_pct>0 else 'down')
            hints.append({'kind': kind, 'branchCode': r['branchCode'], 'branchName': r['branchName'],
                          'a': r['code'], 'aNet': r['net'], 'streak': r['streak'], 'streakCapped':r['streakCapped'],
                          'sameBroker': r.get('relation') == 'sameBroker'})
    for branch, rows in groups.items():
        buys = sorted((r for r in rows if r['net']>0), key=lambda r:-r['net'])
        sells = sorted((r for r in rows if r['net']<0), key=lambda r:r['net'])
        calls, puts = [r for r in buys if r['cp']=='C'], [r for r in buys if r['cp']=='P']
        if calls and puts:
            a,b = calls[0], puts[0]
            hints.append({'kind':'both','branchCode':branch,'branchName':a['branchName'],
                          'a':a['code'],'aNet':a['net'],'b':b['code'],'bNet':b['net'],
                          'sameTerms': bool(a.get('expiry') and a['expiry']==b.get('expiry') and positive(a.get('strike')) and a['strike']==b.get('strike')),
                          'sameBroker': 'sameBroker' in (a.get('relation'), b.get('relation'))})
        for cp in ('C','P'):
            short = [r for r in sells if r['cp']==cp and r.get('remainingDays') is not None and 0<=r['remainingDays']<=SIGNAL_RULES['rollSellDays']]
            long = [r for r in buys if r['cp']==cp and r.get('remainingDays') is not None and r['remainingDays']>=SIGNAL_RULES['rollBuyDays']]
            if short and long:
                a,b = short[0],long[0]
                hints.append({'kind':'roll','branchCode':branch,'branchName':a['branchName'],'cp':cp,
                              'a':a['code'],'aNet':a['net'],'aDays':a['remainingDays'],
                              'aExpiry':a.get('expiry'),'aStrike':a.get('strike'),'aRatio':a.get('ratio'),
                              'b':b['code'],'bNet':b['net'],'bDays':b['remainingDays'],
                              'bExpiry':b.get('expiry'),'bStrike':b.get('strike'),'bRatio':b.get('ratio'),
                              'sameBroker': 'sameBroker' in (a.get('relation'), b.get('relation'))})
    return sorted(hints,key=lambda h:(h['kind'], -abs(h['aNet']), h['branchCode'],h['a']))

