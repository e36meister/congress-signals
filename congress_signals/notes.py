"""Sorting the notes members write on their trades (owner, Oct 27).

Every comment or description on a House or Senate trade filing gets one or more categories:
  * "who decided / why" categories, which may matter for picks (an advisor's scheduled rebalance probably says
    less than a trade the member chose), each tested per trade as a signal before it can count in the score;
  * "detail" categories, which only describe the asset or the trade (option terms, share counts, foreign tickers).

Owner's rule: nothing rare may be hidden in a common category. So
  * a note's main category is its rarest "who/why" category: "Mandatory sale due to an acquisition; decisions are
    delegated to my advisor" is filed as a required sale, not as advisor-managed;
  * a one-off note that only matched common categories (advisor, rebalancing, automatic, account moves) but reads
    unlike the usual wording for them goes on a check list (state/notes_check.json and the app), so rare content
    can't sit unnoticed among the routine ones; so does every note no category fits.
"""
import re

# who decided / why. COMMON are routine; everything else is RARE and wins as the main category.
WHO = [
    ('advisor', 'Advisor or manager decided',
     'professionally managed|managed (account|ira|by)|advisor|adviser|investment (firm|professional|manager)|3rd party|third[- ]party|\\bbroker (purchased|sold|bought)|our broker|discretion|delegated|without (my|our) (input|knowledge|direction)|independently managed|account manager|asset manager|fund manager decided|managed portfolio|wealth manage'),
    ('rebalance', 'Portfolio rebalancing',
     'rebalanc'),
    ('automatic', 'Automatic (dividends, sweeps)',
     'dividend reinvest|reinvest(ment)? (of )?(shares|dividends)|\\breinvest|automatic|\\bsweep|\\bdrip\\b'),
    ('account_move', 'Account opened, closed or moved',
     'account clos|clos(ed|ing) (the )?account|consolidat|new investment firm|transferred (to|from|into)|full liquidation|liquidat(ed|ion) of (the )?account'),
    ('corporate', 'Merger, spin-off or other company action',
     '\\bf/?k/?a\\b|corporate action|merger|acquisition|acquired by|spin[- ]?off|exchanged (out|for)|tender offer|ticker (symbol )?change|formerly known|name change|called by (the )?issuer|redeemed by|\\bmatured\\b|redemption|(received )?in exchange for|\\bexchange for\\b|reverse split|stock split|cash merger'),
    ('comp', 'Pay: options exercised, RSUs, vesting',
     '\\brsus?\\b|restricted stock|(?<!di)\\bvested\\b|\\bvesting\\b|exercised? (of )?(stock )?option|\\besop\\b|employee stock|part of compensation'),
    ('inherit_gift', 'Inherited, gift or donation',
     'inherit|death of|passed away|\\bestate\\b|\\bgift(ed)?\\b|donor[- ]advised|donat|contribution of .* to|charit'),
    ('conflict', 'Sold to avoid a conflict of interest',
     'conflicts? of interest|ethics (committee )?(advice|guidance)|divest\\w* (to|for|because|due)[^.]{0,60}(ethic|conflict|committee|office)|recus'),
    ('error', 'Bought or sold by mistake',
     '\\bin error\\b|\\bmistake|miscommunication|inadvertent|unauthori[sz]ed'),
    ('required', 'Required sale (IRS, plan, forced)',
     'required (irs|minimum|distribution|sale)|\\brmd\\b|mandatory sale|forced sale|margin call'),
    ('plan', 'Pre-set trading plan (10b5-1)',
     '10b5-?1|trading plan|pre-?arranged'),
    ('hedge', 'Hedge, short sale or closing a short',
     'buy(ing)? to cover|short sale|sold short|\\bhedg(e|ing)(?! fund)'),
    ('self', "Member's own call (unsolicited / self-directed)",
     'unsolicited|self[- ]directed|at (my|our) (own )?(direction|request)|i (directed|decided|instructed)'),
    ('amend', 'Correction of an earlier filing',
     'amend|previously reported|correct(ion|ed) (to|of)|late (filing|report)'),
    ('tax', 'Tax reasons (tax-loss harvest)',
     'tax[- ]loss|harvest|tax purposes|wash sale'),
]
COMMON = ("advisor", "rebalance", "automatic", "account_move")
DETAIL = [
    ('retirement', 'In a retirement account (IRA, 401k)',
     '\\bira\\b|401\\(?k\\)?|roth|retirement|\\bsep\\b|pension|403\\(?b\\)?'),
    ('trust', 'Held in a trust',
     '\\btrust\\b|\\bilit\\b|fbo\\b'),
    ('via_entity', 'Held through an LLC, partnership or company',
     "(asset )?held (by|through|in|within) [A-Z][\\w&.,' -]{1,40}(llc|l\\.l\\.c|lp|l\\.p\\.|partners|holdings|inc)\\b|\\b(llc|l\\.p\\.|lllp)\\b"),
    ('private_fund', 'Private fund (hedge, PE, VC)',
     'hedge fund|private equity|venture (capital|fund)|fund manager letter|investment company|private placement|\\bbdc\\b|business development company'),
    ('option_detail', 'Option details (strike, expiry, open/close)',
     '\\bcalls?\\b|\\bputs?\\b|strike|expir|\\bexp\\.?\\b|(buy|sell|bought|sold) (to )?(open|close)'),
    ('size_price', 'Shares, price or gain/loss given',
     '\\b(\\d[\\d,.]*|one|two|three|four|five|six|seven|eight|nine|ten)\\s+(\\w+\\s+)?(additional\\s+)?(shares|sh|units)\\b|@\\s*\\$?\\d|\\bat (an average price of )?\\$\\d|\\bgain\\b|\\bloss\\b|\\$[\\d,]+'),
    ('asset_label', 'Says what the asset is (ADR, ETF, bond...)',
     '\\badr\\b|\\betf\\b|\\beft\\b|mutual fund|\\bbonds?\\b|preferred|treasury|\\bnotes?\\b(?! (about|that|on file|to (self|file)))|structured|common stock|publicly traded stock|^stock$|\\bm/f\\b|\\bcd\\b|annuity|\\bmuni|\\d(\\.\\d+)?%,? due|\\bdue \\d|\\breit\\b|^interest$|swap|company$'),
    ('foreign', 'Foreign listing or ticker',
     'ticker [A-Z0-9.]+ ?(jp|hk|ln|gr|fp|ca|sw|ss|au|ks|im|sm|na|cn)\\b|stock exchange|location: (?!us)'),
    ('cusip', 'Bond/CUSIP code only',
     '^[0-9A-Z]{9}$'),
    ('acct_code', 'Account code only',
     '^[A-Z]{1,4}\\d{1,4}$|^\\d{5,7}$'),
    ('whole', 'Whole position / closed out',
     'entire|full(y)? (sold|sale|liquidation|redemption)|closed position|close(d)? out|sold all|account closing'),
]
LABEL = {k: l for k, l, _ in WHO + DETAIL}
WHO_C = [(k, re.compile(p, re.I)) for k, _, p in WHO]
DET_C = [(k, re.compile(p) if k in ("cusip", "acct_code") else re.compile(p, re.I)) for k, _, p in DETAIL]
NEG = re.compile(r"not (part of|for the purpose of|a result of)[^.;]*", re.I)
CHECK_LEN = 70          # a one-off note in a common category longer than this is checked by the owner


def clean(note):
    return re.sub(r"\s+", " ", str(note or "")).strip()


def tags(note):
    n = NEG.sub(" ", clean(note))       # "Not part of Compensation or ESOP" is not a stock-pay trade
    return [k for k, r in WHO_C if r.search(n)] + [k for k, r in DET_C if r.search(n)]


# most telling first: when a note fits several rare categories, the first of these wins
RARE_ORDER = ("error", "conflict", "required", "self", "plan", "hedge", "tax", "comp", "inherit_gift", "amend", "corporate")


def main(tg):
    """The main 'who/why' category: the rarest one present."""
    rare = [t for t in RARE_ORDER if t in tg]
    if rare:
        return rare[0]
    common = [t for t in tg if t in COMMON]
    return common[0] if common else None


def needs_check(note, tg, seen_count):
    """True when a person should read this note: nothing fits it, or it only matched routine categories but is a
    long one-off (the usual routine notes are short templates that repeat across filings)."""
    who = [t for t in tg if any(t == k for k, _, _ in WHO)]
    if not tg:
        return True
    if who and all(t in COMMON for t in who) and seen_count <= 2 and len(clean(note)) > CHECK_LEN:
        return True
    return False
