"""Bounded root public capture and reproducible static text projection.

No caller supplies success decisions, HTTP responses or callbacks. The root
selects current phase1/inventory and a persistent campaign. Raw response bytes
remain immutable. This module never accesses a browser, cookies, auth, proxies,
forms, caches, mirrors, models or a workbook application.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import ssl
import subprocess
import threading
import sys
import time
from urllib.parse import urljoin, urlsplit, unquote, parse_qsl

ROOT = next(p for p in Path(__file__).resolve().parents if (p/'xl_ast_graph.py').is_file())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.batch import harbor_model_executor as durable
from scripts.batch.harbor_admission import read, record, require
from scripts.batch.harbor_tracker_lifecycle import checked_record

POLICY = 'harbor-static-public-projection/v1'
CAPTURE = 'harbor-public-capture/v2'
MAX_BYTES = 4 * 1024 * 1024
MAX_READS = 3
TIMEOUT = 15


def validator():
    from scripts.batch.harbor_readiness import skill_module
    return skill_module(ROOT, 'profile-mcp-sources', 'validate_source_profiles')


def policy_hash():
    return record(Path(__file__))['sha256']


def value_hash(value):
    return hashlib.sha256(durable.canonical(value)).hexdigest()


def stamp(ns):
    return datetime.fromtimestamp(ns/10**9, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class StaticHTML(HTMLParser):
    """HTML source projection, not a claim about browser/CSS/JS rendering."""
    OMIT = {'script', 'style', 'template', 'noscript', 'nav', 'header', 'footer', 'svg'}
    VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.chunks = []
        self.links = []
        self.barriers = []
        self.main_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        parent_hidden = bool(self.stack and self.stack[-1][1])
        style = attrs.get('style', '').replace(' ', '').lower()
        hidden = parent_hidden or tag in self.OMIT or 'hidden' in attrs or str(attrs.get('aria-hidden','')).lower() == 'true' or any(x in style for x in ('display:none','visibility:hidden'))
        tokens = ' '.join(str(attrs.get(k, '')) for k in ('id','class','src','action')).lower()
        # Auth/challenge/paywall structure is distinguished from a public nav
        # link whose visible label happens to say "sign in".
        in_main = tag in {'main','article'} or any(name in {'main','article'} for name,_ in self.stack)
        if not parent_hidden:
            if tag == 'input' and attrs.get('type','').lower() == 'password':
                self.barriers.append(('auth_login_sso_password',in_main))
            if tag == 'form' and re.search(r'/(login|signin|sign-in|sso|auth)(?:/|$|\?)', attrs.get('action',''), re.I):
                self.barriers.append(('auth_login_sso_password',in_main))
            if any(t in tokens for t in ('cf-chl-', 'challenge-platform', 'g-recaptcha', 'h-captcha')):
                self.barriers.append(('bot_challenge',in_main))
            if attrs.get('data-paywall') == 'true' or attrs.get('data-access') in {'paywall','restricted','subscriber-only'} or set(attrs.get('class','').split()) & {'paywall','paywall-overlay','subscriber-only'}:
                self.barriers.append(('paywall',True))
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])
        if not hidden and tag in {'p','div','main','article','section','h1','h2','h3','li','br','title'}:
            self.chunks.append('\n')
        if tag not in self.VOID:
            require(len(self.stack) < 256, 'HTML nesting exceeds projection bound')
            self.stack.append((tag, hidden))

    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                self.stack = self.stack[:index]
                break
        if tag in {'p','div','main','article','section','h1','h2','h3','li','title'}:
            self.chunks.append('\n')

    def handle_data(self, data):
        if not self.stack or not self.stack[-1][1]:
            self.chunks.append(data)
            if any(name in {'main','article'} for name,_ in self.stack):
                self.main_text.append(data)


def redact(text, forbidden):
    v = validator()
    text = v.CELL_RE.sub('[redacted]', text)
    numbers = set()
    strings = []
    for item in forbidden:
        try:
            number = Decimal(item)
            if not number.is_finite():
                raise InvalidOperation
            numbers.add(number)
        except InvalidOperation:
            strings.append(item)
    # Match complete numbers, allowing sentence punctuation. Equal decimal,
    # grouped, signed, scientific and percent spellings are also removed.
    pattern = r'(?<![\w.])[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?(?!\w|\.\d)'
    def numeric(match):
        number = Decimal(match.group().replace(',', ''))
        percent = match.string[match.end():].startswith('%')
        # The independent validator also checks literal numeric fragments, e.g.
        # forbidden "1" at the start of "1,327.163". Remove the whole token
        # when that stricter check matches; retain the original response bytes.
        return '[redacted]' if (number in numbers or (percent and number/100 in numbers)
            or v.leaked_value(match.group(), forbidden) is not None) else match.group()
    text = re.sub(pattern, numeric, text)
    for item in sorted(strings, key=lambda x: (-len(x), x)):
        text = re.sub(re.escape(item), '[redacted]', text, flags=re.I)
    require(v.leaked_value(text, forbidden) is None and not v.CELL_RE.search(text), 'projection redaction did not close')
    return text


def project(raw, *, content_type, forbidden, status=200, content_encoding='identity'):
    require(isinstance(raw, bytes) and len(raw) <= MAX_BYTES, 'public body exceeds limit')
    disposition = 'ok'
    if status in (401,403):
        disposition = 'http_' + str(status)
    elif status != 200:
        disposition = 'unreachable'
    kind = content_type.split(';',1)[0].strip().lower() if isinstance(content_type,str) else ''
    if content_encoding.lower() not in {'', 'identity'}:
        disposition = disposition if disposition != 'ok' else 'unsupported_content'
    if kind not in {'text/html','text/plain'}:
        disposition = disposition if disposition != 'ok' else 'unsupported_content'
    charset = re.search(r'charset\s*=\s*["\']?([^\s;"\']+)', content_type or '', re.I)
    if charset and charset.group(1).lower() not in {'utf-8','utf8','us-ascii','ascii'}:
        disposition = disposition if disposition != 'ok' else 'unsupported_content'
    text = ''
    links = []
    if disposition == 'ok':
        try:
            decoded = raw.decode('utf-8', errors='strict')
        except UnicodeError:
            disposition = 'unsupported_content'
        else:
            if kind == 'text/html':
                parser = StaticHTML()
                parser.feed(decoded)
                parser.close()
                barriers = {reason for reason,in_main in parser.barriers if in_main or not ''.join(parser.main_text).strip()}
                if barriers:
                    disposition = sorted(barriers)[0]
                text = ''.join(parser.chunks)
                links = parser.links
            else:
                text = decoded
            text = '\n'.join(' '.join(line.split()) for line in text.splitlines() if line.strip())
            # Exact standalone gate messages are not ordinary nav/article words.
            phrases = {
                'auth_login_sso_password': r'(?:sign in|log in|login|password required)(?: to (?:continue|access this (?:page|content)))?[.!]?',
                'bot_challenge': r'(?:verify (?:that )?you are human|checking your browser|complete the captcha to continue)[.!]?',
                'paywall': r'(?:subscribe to continue reading|this content is (?:available to subscribers only|subscriber-only))[.!]?',
            }
            for reason, pattern in phrases.items():
                if any(re.fullmatch(pattern, block, re.I) for block in text.splitlines()):
                    disposition = reason
            if not text:
                disposition = disposition if disposition != 'ok' else 'unsupported_content'
    if disposition != 'ok':
        text = ''
        links = []  # No source detail extraction from a blocked response.
    else:
        text = redact(text, forbidden)
    return {'schema_version':POLICY, 'policy_sha256':policy_hash(),
            'raw_sha256':hashlib.sha256(raw).hexdigest(),
            'forbidden_sha256':value_hash(sorted(forbidden)),
            'content_type':kind, 'disposition':disposition, 'text':text, 'links':links}


def allowed(url, canonical, forbidden):
    v = validator()
    require(not v.public_url_error(url, require_canonical=False), 'nonpublic/authenticated URL')
    require(v.origin(url) == v.origin(canonical), 'cross-origin public request refused')
    decoded = url
    for _ in range(4):
        new = unquote(decoded)
        if new == decoded:
            break
        decoded = new
    parts = urlsplit(decoded)
    require(not v.AUTH_PATH_RE.search(parts.path), 'authentication URL refused before public read')
    keys = {key.casefold().replace('-', '_') for key,_ in parse_qsl(parts.query)}
    require(not keys & {'api_key','access_token','id_token','token','password','passwd','session','sessionid','signature','sig','x_amz_signature','x_amz_credential'}, 'credential-bearing query refused')
    require(redact(decoded, forbidden) == decoded, 'private value/cell in public request URL')
    return v.canonicalize_url(url)


def public_address(value):
    address = ipaddress.ip_address(value)
    return address.is_global and not address.is_multicast


DNS_PROGRAM = "import socket,json,sys; print(json.dumps(sorted({r[4][0] for r in socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),type=socket.SOCK_STREAM)})))"


def global_addresses(host, port, *, timeout=TIMEOUT):
    # libc DNS can block independently of a socket timeout. A fixed isolated
    # Python resolver subprocess gives it a real deadline and no HTTP ability.
    require(timeout > 0, 'public request deadline exhausted')
    result = subprocess.run([sys.executable, '-I', '-c', DNS_PROGRAM, host, str(port)],
                            capture_output=True, text=True, check=True, timeout=timeout)
    addresses = json.loads(result.stdout)
    require(isinstance(addresses, list) and addresses and all(isinstance(a,str) and public_address(a) for a in addresses), 'DNS resolves to non-global address')
    require(addresses == sorted(set(addresses)), 'DNS address inventory is not canonical')
    return addresses


def complete_body(raw, content_length):
    if len(raw) > MAX_BYTES:
        return False
    if content_length is None:
        return True
    return isinstance(content_length, str) and content_length.isascii() and content_length.isdecimal() and len(raw) == int(content_length)


def exchange(url):
    """One pinned-address GET. No redirect handler, proxy, cookie or auth layer."""
    deadline = time.monotonic() + TIMEOUT
    def remaining():
        value = deadline - time.monotonic()
        require(value > 0, 'public request deadline exhausted')
        return value
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    require(port in {80,443}, 'unsupported public port')
    addresses = global_addresses(parsed.hostname, port, timeout=remaining())
    chosen = addresses[0]
    conn = http.client.HTTPConnection(parsed.hostname, port, timeout=TIMEOUT)
    sock = socket.create_connection((chosen, port), timeout=remaining())
    active = [sock]
    def interrupt_socket():
        try:
            active[0].shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    # A peer sending one byte repeatedly must not extend the total header/body
    # deadline. This timer can only close this one fresh request socket.
    timer = None
    try:
        timer = threading.Timer(remaining(), interrupt_socket)
        timer.daemon = True
        timer.start()
        sock.settimeout(remaining())
        if parsed.scheme == 'https':
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)
        active[0] = sock
        sock.settimeout(remaining())
        conn.sock = sock
        target = parsed.path or '/'
        if parsed.query:
            target += '?' + parsed.query
        conn.request('GET', target, headers={'Accept':'text/html,text/plain', 'Accept-Encoding':'identity', 'User-Agent':'HarborPublicSourceProfiler/1'})
        response = conn.getresponse()
        complete = True
        try:
            raw = response.read(MAX_BYTES+1)
        except http.client.IncompleteRead as exc:
            raw = exc.partial
            complete = False
        remaining()
        content_length = response.getheader('Content-Length')
        return {'status':response.status, 'content_type':response.getheader('Content-Type',''),
                'location':response.getheader('Location'), 'body':raw,
                'content_encoding':response.getheader('Content-Encoding','identity'),
                'body_complete':complete and complete_body(raw,content_length),
                'content_length':content_length,
                'addresses':addresses, 'connected_address':chosen}
    finally:
        if timer is not None:
            timer.cancel()
        conn.close()
        sock.close()


@dataclass(frozen=True)
class CaptureSelection:
    workbook_id: str
    original_source: dict
    phase1: dict
    inventory: dict
    campaign: Path


def context(selection):
    require(isinstance(selection,CaptureSelection), 'root-selected capture context required')
    require(re.fullmatch(r'[0-9]{4}',selection.workbook_id), 'four-digit workbook required')
    for binding in (selection.original_source,selection.phase1,selection.inventory):
        checked_record(binding)
    spec = read(selection.phase1)
    require(spec.get('environment_id') == 'fcp-'+selection.workbook_id, 'foreign phase1 workbook')
    from scripts.batch.harbor_profile_readiness import worklist
    v = validator()
    forbidden = v.forbidden_values([Path(selection.phase1['path']),Path(selection.inventory['path'])])
    sources = worklist(spec,forbidden,root=ROOT)
    for source in sources:
        allowed(source['canonical_url'],source['canonical_url'],forbidden)
    return sources,forbidden


def capture_source(selection, source, forbidden):
    canonical = source['canonical_url']
    scope = value_hash({'workbook':selection.workbook_id,'original_sha256':selection.original_source['sha256'],'canonical_url':canonical})
    root = durable.safe(selection.campaign)/scope
    plan = {'schema_version':'harbor-public-capture-plan/v1','selection':{
        **asdict(selection),'campaign':str(selection.campaign)},'source':source,'policy_sha256':policy_hash(),
        'max_http_exchanges':MAX_READS,'timeout_seconds':TIMEOUT,'max_response_bytes':MAX_BYTES,
        'automatic_retry':False}
    with durable.scope_lock(root):
        if (root/'plan.json').exists():
            require(read(record(root/'plan.json'))==plan, 'capture context changed; no budget reset or automatic refetch')
            if not (root/'outcome.json').exists():
                return {'status':'STOPPED','reason':'unknown_completion','captures':[], 'journal':str(root)}
            outcome = read(record(root/'outcome.json'))
            actual = {p.name:record(p)['sha256'] for p in root.iterdir() if p.is_file() and p.name not in {'scope.lock','outcome.json'}}
            require(set(outcome)=={'schema_version','status','reason','captures','http_exchanges_used','automatic_retry','artifacts'}
                    and outcome['schema_version']=='harbor-public-capture-outcome/v1'
                    and outcome['automatic_retry'] is False, 'invalid capture outcome')
            require(outcome['artifacts']==actual, 'capture journal changed')
            used = outcome['http_exchanges_used']
            require(type(used) is int and 0 <= used <= MAX_READS, 'capture consumption changed')
            require({p.name for p in root.glob('http-*.intent.json')} ==
                    {f'http-{number:02d}.intent.json' for number in range(1,used+1)}, 'HTTP consumption differs from retained intents')
            require(all(Path(binding['path']).parent == root for binding in outcome['captures']), 'capture escaped owning journal')
            require(outcome['status'] in {'COMPLETE','STOPPED'}, 'invalid capture terminal state')
            if outcome['status']=='COMPLETE':
                require(outcome['reason'] is None, 'completed capture retains failure')
                pages(outcome['captures'],canonical,forbidden)
            else:
                require(isinstance(outcome['reason'],str) and outcome['reason'], 'stopped capture lacks reason')
            return outcome
        durable.save(root/'plan.json',plan)
        queue = [canonical]
        records = []
        exchanges = 0
        first_links = set()
        reason = None
        try:
            while queue and exchanges < MAX_READS:
                requested = queue.pop(0)
                url = allowed(requested,canonical,forbidden)
                redirects = []
                receipts = []
                start = time.time_ns()
                while True:
                    require(exchanges < MAX_READS, 'HTTP read budget exhausted before final response')
                    exchanges += 1
                    prefix = root/f'http-{exchanges:02d}'
                    intent = {'schema_version':'harbor-public-http-intent/v1','url':url,'method':'GET','started_at_ns':time.time_ns(),'policy_sha256':policy_hash(),'authentication':'none','cookies':False}
                    durable.save(prefix.with_suffix('.intent.json'),intent)
                    response = exchange(url)
                    finished = time.time_ns()
                    durable.retain(prefix.with_suffix('.body'),response['body'])
                    receipt = {k:response[k] for k in ('status','content_type','content_encoding','body_complete','content_length','location','addresses','connected_address')}
                    receipt.update(schema_version='harbor-public-http-result/v1',intent=record(prefix.with_suffix('.intent.json')),body=record(prefix.with_suffix('.body')),completed_at_ns=finished)
                    durable.save(prefix.with_suffix('.result.json'),receipt)
                    receipts.append(record(prefix.with_suffix('.result.json')))
                    require(response['body_complete'] is True, 'public response exceeded retained byte bound')
                    if response['status'] in {301,302,303,307,308}:
                        require(isinstance(response['location'],str) and response['location'], 'redirect lacks location')
                        redirected = allowed(urljoin(url,response['location']),canonical,forbidden)
                        require(redirected not in [requested,*redirects], 'redirect loop')
                        redirects.append(redirected)
                        url = redirected
                        continue
                    break
                projection = project(response['body'],content_type=response['content_type'],forbidden=forbidden,status=response['status'],content_encoding=response['content_encoding'])
                number = len(records)+1
                projection_path = root/f'page-{number:02d}.projection.json'
                durable.save(projection_path,projection)
                cap = {'schema_version':CAPTURE,'requested_url':requested,'final_url':url,'redirects':redirects,
                    'status':response['status'],'content_type':projection['content_type'],
                    'started_at_ns':start,'completed_at_ns':finished,'accessed_at':stamp(start),
                    'authentication':'none','body':receipt['body'],'projection':record(projection_path),
                    'projection_policy_sha256':policy_hash(),'http_exchanges':receipts}
                cap_path = root/f'page-{number:02d}.capture.json'
                durable.save(cap_path,cap)
                records.append(record(cap_path))
                if projection['disposition'] != 'ok':
                    reason = projection['disposition']
                    break
                if number == 1:
                    candidates = []
                    for href in projection['links']:
                        try:
                            link = allowed(urljoin(url,href),canonical,forbidden)
                        except ValueError:
                            continue
                        if link != canonical and link not in redirects:
                            candidates.append(link)
                    first_links = set(candidates)
                    queue = sorted(first_links)[:MAX_READS-exchanges]
            if reason is None:
                require(records, 'no completed public capture')
                _derive_pages(records,canonical,forbidden)
        except Exception as exc:
            reason = 'capture_failed'
            durable.save(root/'failure.json',{'type':type(exc).__name__,'message':str(exc),'automatic_retry':False})
        outcome = {'schema_version':'harbor-public-capture-outcome/v1','status':'STOPPED' if reason else 'COMPLETE',
            'reason':reason,'captures':records,'http_exchanges_used':exchanges,'automatic_retry':False,
            'artifacts':{p.name:record(p)['sha256'] for p in root.iterdir() if p.is_file() and p.name!='scope.lock'}}
        durable.save(root/'outcome.json',outcome)
        return outcome


def pages(bindings, canonical, forbidden):
    """Consume a complete source attempt, including every actual HTTP exchange."""
    require(isinstance(bindings, (list, tuple)) and bindings, 'completed public source evidence required')
    root = durable.safe(Path(bindings[0]['path']).parent)
    require(all(Path(binding['path']).parent == root for binding in bindings), 'mixed capture attempts')
    outcome_binding = record(root/'outcome.json')
    outcome = read(outcome_binding)
    require(set(outcome) == {'schema_version','status','reason','captures','http_exchanges_used','automatic_retry','artifacts'}
            and outcome['schema_version'] == 'harbor-public-capture-outcome/v1', 'invalid source capture outcome')
    require(outcome['status'] == 'COMPLETE' and outcome['reason'] is None and outcome['automatic_retry'] is False,
            'whole source capture did not complete')
    require(outcome['captures'] == list(bindings), 'capture selection omits or substitutes source evidence')
    actual = {p.name:record(p)['sha256'] for p in root.iterdir() if p.is_file() and p.name not in {'scope.lock','outcome.json'}}
    require(outcome['artifacts'] == actual, 'capture attempt evidence changed')
    plan_binding = record(root/'plan.json'); plan = read(plan_binding)
    require(plan.get('schema_version') == 'harbor-public-capture-plan/v1' and
            plan['source']['canonical_url'] == canonical and plan['policy_sha256'] == policy_hash(), 'capture source policy changed')
    used = outcome['http_exchanges_used']
    require(type(used) is int and 1 <= used <= MAX_READS, 'invalid actual read count')
    require({p.name for p in root.glob('http-*.intent.json')} ==
            {f'http-{number:02d}.intent.json' for number in range(1,used+1)}, 'incomplete HTTP intent inventory')
    receipts = [row for binding in bindings for row in read(binding)['http_exchanges']]
    require(len(receipts) == used and {Path(r['path']) for r in receipts} == set(root.glob('http-*.result.json')),
            'completed pages do not cover every HTTP exchange')
    projected, evidence = _derive_pages(bindings, canonical, forbidden)
    return projected, [outcome_binding, plan_binding, *evidence]


def _derive_pages(bindings, canonical, forbidden):
    """Re-derive all page bytes; the producer also uses this before sealing."""
    require(1 <= len(bindings) <= MAX_READS, 'public page budget absent or exceeded')
    v = validator()
    result = []
    evidence = []
    seen_http = set()
    links = set()
    previous_end = 0
    previous_http_end = 0
    for index,binding in enumerate(bindings):
        cap = read(binding)
        require(set(cap)=={'schema_version','requested_url','final_url','redirects','status','content_type','started_at_ns','completed_at_ns','accessed_at','authentication','body','projection','projection_policy_sha256','http_exchanges'}, 'invalid v2 public capture schema')
        require(cap['schema_version']==CAPTURE and cap['authentication']=='none' and cap['projection_policy_sha256']==policy_hash(),'capture projection policy/auth changed')
        require(cap['requested_url']==canonical if index==0 else cap['requested_url'] in links, 'capture not canonical or directly linked')
        start,end=cap['started_at_ns'],cap['completed_at_ns']
        require(type(start) is int and type(end) is int and 0 < start <= end and start >= previous_end,'capture chronology changed')
        require(cap['accessed_at']==stamp(start),'capture timestamp changed')
        previous_end=end
        receipts=cap['http_exchanges']
        require(isinstance(receipts,list) and receipts, 'actual HTTP evidence required')
        request_url=cap['requested_url']
        expected_redirects=[]
        last=None
        for receipt in receipts:
            require(receipt['path'] not in seen_http,'HTTP evidence reused across page budgets')
            seen_http.add(receipt['path']);require(len(seen_http)<=MAX_READS,'actual HTTP exchange budget exceeded')
            row=read(receipt);intent=read(row['intent'])
            require(set(intent)=={'schema_version','url','method','started_at_ns','policy_sha256','authentication','cookies'} and intent['schema_version']=='harbor-public-http-intent/v1','invalid HTTP intent')
            require(intent['method']=='GET' and intent['authentication']=='none' and intent['cookies'] is False and intent['policy_sha256']==policy_hash(),'HTTP method/auth/policy differs')
            require(intent['url']==request_url and allowed(request_url,canonical,forbidden)==request_url,'HTTP request target differs')
            require(set(row)=={'schema_version','status','content_type','content_encoding','body_complete','content_length','location','addresses','connected_address','intent','body','completed_at_ns'} and row['schema_version']=='harbor-public-http-result/v1','invalid HTTP result')
            require(max(start,previous_http_end) <= intent['started_at_ns'] <= row['completed_at_ns'] <= end,'HTTP observation chronology differs')
            previous_http_end = row['completed_at_ns']
            require(row['addresses'] and row['connected_address'] in row['addresses'] and all(public_address(a) for a in row['addresses']),'HTTP connected address is not public')
            require(row['body_complete'] is True, 'incomplete public response')
            checked_record(row['body'])
            require(complete_body(Path(row['body']['path']).read_bytes(),row['content_length']), 'HTTP body length differs from response header')
            evidence += [receipt,row['intent'],row['body']]
            if row['status'] in {301,302,303,307,308}:
                request_url=allowed(urljoin(request_url,row['location']),canonical,forbidden)
                require(request_url not in [cap['requested_url'],*expected_redirects],'redirect loop')
                expected_redirects.append(request_url)
            else:
                require(receipt==receipts[-1],'HTTP reads continued after final response')
            last=row
        require(last['status'] not in {301,302,303,307,308} and expected_redirects==cap['redirects'] and request_url==cap['final_url'],'redirect/final identity differs')
        require(last['body']==cap['body'] and last['status']==cap['status'] and last['completed_at_ns']==end,'capture differs from HTTP response')
        projection=project(Path(cap['body']['path']).read_bytes(),content_type=last['content_type'],status=last['status'],forbidden=forbidden,content_encoding=last['content_encoding'])
        require(read(cap['projection'])==projection and cap['content_type']==projection['content_type'],'derived projection/content changed')
        require(projection['disposition']=='ok','public access unavailable: '+projection['disposition'])
        if index==0:
            links={v.canonicalize_url(urljoin(cap['final_url'],href)) for href in projection['links']}
        result.append({k:cap[k] for k in ('requested_url','final_url','status','content_type','accessed_at')} | {'body':projection['text']})
        evidence += [binding,cap['body'],cap['projection']]
    return result,evidence


def collect(selection):
    sources,forbidden=context(selection)
    outcomes=[]
    rows=[]
    for source in sources:
        outcome=capture_source(selection,source,forbidden)
        outcomes.append(outcome)
        rows.append({'canonical_url':source['canonical_url'],'captures':outcome['captures']})
    # No success flag may substitute for later independent profile validation.
    return {'schema_version':'harbor-profile-captures/v1','sources':rows},outcomes


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection',type=Path,required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--capture',action='store_true',help='Explicit root-authorized public GET dispatch; omitted means prepare only')
    args=parser.parse_args(argv)
    value=read({'path':str(args.selection.absolute()),'sha256':args.sha256})
    require(set(value)=={'workbook_id','original_source','phase1','inventory','campaign'},'closed capture selection required')
    selection=CaptureSelection(**{**value,'campaign':Path(value['campaign'])})
    sources,_=context(selection)
    if args.capture:
        captures,outcomes=collect(selection)
        durable.save(args.out, captures)
        print(json.dumps({'capture_inventory':record(args.out),'sources':len(sources),'complete_sources':sum(r['status']=='COMPLETE' for r in outcomes),'model_calls':0}))
    else:
        durable.save(args.out,{'schema_version':'harbor-public-capture-preparation/v1','selection':record(args.selection),'worklist':sources,'policy_sha256':policy_hash(),'max_http_exchanges_per_source':MAX_READS,'network_calls':0})
        print(json.dumps(record(args.out)))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
