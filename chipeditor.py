#!/usr/bin/env python3
"""
EZP XPro V1  database.dat  editor  —  curses TUI
Run:  python3 chipeditor.py database.dat

Keys:
  TAB / Shift-TAB   cycle panes (Categories → Manufacturers → Chips → Fields)
  ↑ ↓               navigate
  PgUp / PgDn       fast scroll
  Enter / →         enter next pane / begin editing (Fields pane)
  ESC / ←           cancel / go back
  s                 save
  a                 add chip to current manufacturer group
  n                 add new manufacturer after current group
  d                 delete chip (not boundary chips)
  c                 copy chip
  v                 verify roundtrip
  q                 quit
"""
import sys, os, copy, curses, re

# ── Cipher ───────────────────────────────────────────────────────────────────
ENCODE = {
    ' ':0x0a, ',':0x23, '/':0x6f,
    '0':0x67, '1':0x0e, '2':0x1a, '3':0x5a, '4':0x65,
    '5':0x6a, '6':0x75, '7':0x32, '8':0x62, '9':0x53,
    'A':0x42, 'B':0x33, 'C':0x7a, 'D':0x47, 'E':0x45,
    'F':0x37, 'G':0x57, 'H':0x35, 'I':0x55, 'J':0x66,
    'K':0x63, 'L':0x73, 'M':0x1e, 'N':0x3e, 'O':0x4e,
    'P':0x5e, 'Q':0x7e, 'R':0x06, 'S':0x36, 'T':0x56,
    'U':0x52, 'V':0x76, 'W':0x1f, 'X':0x2f, 'Y':0x3f,
    'Z':0x5f,
}
DECODE = {v: k for k, v in ENCODE.items()}
DECODE[0x32] = '7'
DECODE[0x72] = '/'

CAT_MARKER = 0x4a
REC_SEP    = bytes([0x50, 0x20, 0x0c])  # record/chip separator
MFR_SEP    = bytes([0x50, 0x20, 0x3a])  # manufacturer name separator
FIELD_SEP  = 0x23

FIELD_NAMES = [
    'Name','PageSize','SectorSize','TotalSize',
    'N','Config','JEDEC','JEDEC2orN','VCC','Flags',
]
VALID_CHARS = set(ENCODE.keys())

# ── Codec ─────────────────────────────────────────────────────────────────────
def decode_field(raw: bytes) -> str:
    return ''.join(DECODE.get(b, f'[{b:02x}]') for b in raw)

def encode_field(s: str) -> bytes:
    out = bytearray()
    for tok in re.split(r'(\[[0-9a-fA-F]{2}\])', s):
        if re.fullmatch(r'\[[0-9a-fA-F]{2}\]', tok):
            out.append(int(tok[1:3], 16))
        else:
            for ch in tok.upper():
                b = ENCODE.get(ch)
                if b is not None:
                    out.append(b)
    return bytes(out)

def valid_name(s: str) -> str:
    return ''.join(c for c in s.upper() if c in VALID_CHARS)

# ── Database ──────────────────────────────────────────────────────────────────
def parse_db(data: bytes) -> list:
    cat_pos = [i for i, b in enumerate(data) if b == CAT_MARKER]
    segs = []
    for idx, start in enumerate(cat_pos):
        end   = cat_pos[idx+1] if idx+1 < len(cat_pos) else len(data)
        seg   = data[start:end]
        sp    = seg.find(REC_SEP)
        if sp == -1:
            segs.append({'header': seg, 'chips': []}); continue
        chips = [p.split(bytes([FIELD_SEP]))
                 for p in seg[sp:].split(REC_SEP) if p]
        segs.append({'header': seg[:sp], 'chips': chips})
    return segs

def encode_db(segs: list) -> bytes:
    out = bytearray()
    for seg in segs:
        out += seg['header']
        for chip in seg['chips']:
            out += REC_SEP
            out += bytes([FIELD_SEP]).join(chip)
    return bytes(out)

def get_field(chip, idx):
    return decode_field(chip[idx]) if idx < len(chip) else ''

def set_field(chip, idx, value):
    while len(chip) <= idx: chip.append(b'')
    chip[idx] = encode_field(value)

# ── Manufacturer groups ───────────────────────────────────────────────────────
def get_mfr_groups(seg: dict) -> list:
    hdr = decode_field(seg['header'])
    if '[50]' in hdr:
        raw_after = hdr.split('[50]')[1].replace('[20]','').replace('[3a]','').rstrip(',')
        first_mfr = re.sub(r'\[[0-9a-f]{2}\]', '', raw_after).strip()
    else:
        first_mfr = '?'
    groups = []; cur_mfr = first_mfr; cur_chips = []
    for i, chip in enumerate(seg['chips']):
        cur_chips.append(i)
        raw_flags = chip[9] if len(chip) > 9 else b''
        if MFR_SEP in raw_flags:
            suffix   = raw_flags[raw_flags.index(MFR_SEP) + len(MFR_SEP):]
            next_mfr = re.sub(r'\[[0-9a-f]{2}\]', '', decode_field(suffix)).strip()
            groups.append({'name': cur_mfr, 'chip_indices': list(cur_chips)})
            cur_mfr = next_mfr; cur_chips = []
    if cur_chips:
        groups.append({'name': cur_mfr, 'chip_indices': cur_chips})
    return groups

def _raw_flags_clean(chip) -> bytes:
    """Raw bytes of Flags BEFORE any MFR_SEP — never decoded/re-encoded."""
    raw = chip[9] if len(chip) > 9 else b''
    if MFR_SEP in raw:
        return raw[:raw.index(MFR_SEP)]
    return raw

def flags_clean(chip) -> str:
    return decode_field(_raw_flags_clean(chip))

def flags_mfr(chip) -> str:
    raw = chip[9] if len(chip) > 9 else b''
    if MFR_SEP in raw:
        suffix = raw[raw.index(MFR_SEP) + len(MFR_SEP):]
        return re.sub(r'\[[0-9a-f]{2}\]', '', decode_field(suffix)).strip()
    return ''

def set_flags(chip, clean_val: str, next_mfr: str = ''):
    """
    Set Flags field.  clean_val is the editable part (e.g. '000').
    next_mfr is the manufacturer name to announce (boundary marker).
    Uses encode_field() only for clean_val; appends raw MFR_SEP bytes directly.
    """
    while len(chip) <= 9: chip.append(b'')
    raw = encode_field(clean_val)
    if next_mfr:
        raw += MFR_SEP + encode_field(next_mfr)
    chip[9] = raw

def add_chip_to_group(seg: dict, group_idx: int, groups: list) -> int:
    g        = groups[group_idx]
    is_last  = (group_idx == len(groups) - 1)
    insert_at = g['chip_indices'][-1] + 1 if is_last else g['chip_indices'][-1]
    new_chip = [b''] * 10
    set_field(new_chip, 0, 'NEW CHIP')
    set_field(new_chip, 1, '256')
    set_field(new_chip, 2, 'N')
    set_field(new_chip, 3, 'N')
    set_field(new_chip, 4, 'N')
    set_field(new_chip, 5, '324')
    set_field(new_chip, 6, 'N')
    set_field(new_chip, 7, 'N')
    set_field(new_chip, 8, '3')
    set_field(new_chip, 9, '000')
    seg['chips'].insert(insert_at, new_chip)
    return insert_at

def add_manufacturer_after(seg: dict, after_group_idx: int, groups: list,
                            new_mfr_name: str) -> int:
    """
    Insert a new manufacturer group after after_group_idx.
    Correctly maintains the boundary-marker chain using raw byte preservation.
    Returns the absolute chip index of the first new chip.
    """
    g         = groups[after_group_idx]
    last_abs  = g['chip_indices'][-1]
    last_chip = seg['chips'][last_abs]

    # Capture what the boundary chip currently announces (OLD next group name raw bytes)
    old_raw   = last_chip[9] if len(last_chip) > 9 else b''
    if MFR_SEP in old_raw:
        old_next_raw = old_raw[old_raw.index(MFR_SEP) + len(MFR_SEP):]  # raw encoded name
        clean_raw    = old_raw[:old_raw.index(MFR_SEP)]
    else:
        old_next_raw = b''
        clean_raw    = old_raw

    # Boundary chip now announces the NEW manufacturer (encode new name fresh)
    while len(last_chip) <= 9: last_chip.append(b'')
    last_chip[9] = clean_raw + MFR_SEP + encode_field(new_mfr_name)

    # New blank chip for the new group; it announces the OLD next group verbatim
    new_chip = [b''] * 10
    set_field(new_chip, 0, 'NEW CHIP')
    set_field(new_chip, 1, '256')
    set_field(new_chip, 2, 'N')
    set_field(new_chip, 3, 'N')
    set_field(new_chip, 4, 'N')
    set_field(new_chip, 5, '324')
    set_field(new_chip, 6, 'N')
    set_field(new_chip, 7, 'N')
    set_field(new_chip, 8, '3')
    if old_next_raw:
        # Restore old announcement verbatim (bit-perfect)
        new_chip[9] = encode_field('000') + MFR_SEP + old_next_raw
    else:
        new_chip[9] = encode_field('000')

    insert_at = last_abs + 1
    seg['chips'].insert(insert_at, new_chip)
    return insert_at

def category_label(header: bytes) -> str:
    decoded = decode_field(header)
    name    = decoded.split('[50]')[0] if '[50]' in decoded else decoded
    return name.replace('[4a]','').replace('[02]','(').replace('[12]',')').strip()

# ── Utilities ─────────────────────────────────────────────────────────────────
def clamp(v, lo, hi): return max(lo, min(hi, v))
def pad(s, w): return str(s)[:w].ljust(w)

# ── App ───────────────────────────────────────────────────────────────────────
class App:
    P_CAT=0; P_MFR=1; P_CHIP=2; P_FIELD=3
    PM_NONE=0; PM_NEW_MFR=1

    def __init__(self, scr, path):
        self.scr  = scr; self.path = path
        self.segs = []; self.ci=0; self.mi=0; self.ki=0; self.fi=0
        self.pane = self.P_CAT
        self.status=''; self.dirty=False
        self.editing: str|None = None
        self.prompt_mode = self.PM_NONE
        self.prompt_buf  = ''
        self._cscr=self._mscr=self._kscr=0
        self._qc=False
        self._load()
        curses.start_color(); curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK,  curses.COLOR_CYAN)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        curses.init_pair(3, curses.COLOR_GREEN,  -1)
        curses.init_pair(4, curses.COLOR_RED,    -1)
        curses.init_pair(5, curses.COLOR_CYAN,   -1)

    def _load(self):
        self.segs = parse_db(open(self.path,'rb').read())
        n = sum(len(s['chips']) for s in self.segs)
        self.status = f'Loaded {os.path.basename(self.path)}  ({len(self.segs)} categories, {n} chips)'

    def _save(self):
        data = encode_db(self.segs)
        open(self.path,'wb').write(data)
        self.dirty = False
        self.status = f'Saved → {self.path}  ({len(data):,} bytes)'

    def _verify(self):
        orig  = open(self.path,'rb').read()
        reenc = encode_db(self.segs)
        if orig == reenc:
            self.status = '✓ Roundtrip OK — file matches disk exactly'; return
        for i,(a,b) in enumerate(zip(orig,reenc)):
            if a!=b:
                self.status=f'✗ Diff at byte {i}: disk=0x{a:02x} mem=0x{b:02x}'; return
        self.status=f'✗ Length: disk={len(orig)} mem={len(reenc)}'

    @property
    def seg(self):
        if not self.segs: return None
        return self.segs[clamp(self.ci,0,len(self.segs)-1)]

    def _groups(self):
        s=self.seg; return get_mfr_groups(s) if s else []

    @property
    def group(self):
        g=self._groups()
        return g[clamp(self.mi,0,len(g)-1)] if g else None

    @property
    def chip(self):
        g=self.group; s=self.seg
        if not g or not s: return None
        ki=clamp(self.ki,0,len(g['chip_indices'])-1)
        return s['chips'][g['chip_indices'][ki]]

    def draw(self):
        H,W=self.scr.getmaxyx()
        CW=22; MW=20; FW=42; KW=max(8,W-CW-MW-FW-5)
        ws=[curses.newwin(H-2,CW,0,0),
            curses.newwin(H-2,MW,0,CW),
            curses.newwin(H-2,KW,0,CW+MW),
            curses.newwin(H-2,FW,0,CW+MW+KW),
            curses.newwin(2,W,H-2,0)]
        self._draw_cats(ws[0],H-2,CW)
        self._draw_mfrs(ws[1],H-2,MW)
        self._draw_chips(ws[2],H-2,KW)
        self._draw_fields(ws[3],H-2,FW)
        self._draw_status(ws[4],W)
        self.scr.noutrefresh()
        for w in ws: w.noutrefresh()
        curses.doupdate()

    def _sa(self,sel,active):
        if sel and active: return curses.color_pair(1)|curses.A_BOLD
        if sel:            return curses.A_REVERSE
        return curses.A_NORMAL

    def _draw_cats(self,w,H,W):
        w.erase(); w.box(); w.addstr(0,2,' Category ',curses.A_BOLD)
        ih=H-2
        self._cscr=clamp(self._cscr,max(0,self.ci-ih+1),self.ci)
        for r in range(ih):
            i=self._cscr+r
            if i>=len(self.segs): break
            lbl=category_label(self.segs[i]['header'])
            nc=len(self.segs[i]['chips'])
            try: w.addstr(r+1,1,pad(f'{lbl}({nc})',W-2),self._sa(i==self.ci,self.pane==self.P_CAT))
            except: pass

    def _draw_mfrs(self,w,H,W):
        w.erase(); w.box(); w.addstr(0,2,' Mfr ',curses.A_BOLD)
        ih=H-2; groups=self._groups()
        self._mscr=clamp(self._mscr,max(0,self.mi-ih+1),self.mi)
        for r in range(ih):
            i=self._mscr+r
            if i>=len(groups): break
            g=groups[i]
            try: w.addstr(r+1,1,pad(f'{g["name"]}({len(g["chip_indices"])})',W-2),
                          self._sa(i==self.mi,self.pane==self.P_MFR))
            except: pass

    def _draw_chips(self,w,H,W):
        w.erase(); w.box(); w.addstr(0,2,' Chips ',curses.A_BOLD)
        ih=H-2; g=self.group; s=self.seg
        if not g or not s: return
        idxs=g['chip_indices']
        self._kscr=clamp(self._kscr,max(0,self.ki-ih+1),self.ki)
        for r in range(ih):
            i=self._kscr+r
            if i>=len(idxs): break
            chip=s['chips'][idxs[i]]
            name=get_field(chip,0).replace('[02]','(').replace('[12]',')')
            size=get_field(chip,3)
            is_bnd=(MFR_SEP in (chip[9] if len(chip)>9 else b''))
            try: w.addstr(r+1,1,pad(f'{"►" if is_bnd else " "}{name} {size}',W-2),
                          self._sa(i==self.ki,self.pane==self.P_CHIP))
            except: pass

    def _draw_fields(self,w,H,W):
        w.erase(); w.box(); w.addstr(0,2,' Fields  Enter=edit ',curses.A_BOLD)
        c=self.chip
        if not c: return
        vw=W-14
        for r,fn in enumerate(FIELD_NAMES):
            if r+1>=H-1: break
            is_cur=(self.pane==self.P_FIELD and r==self.fi)
            if r==9:
                if is_cur and self.editing is not None:
                    val=self.editing
                else:
                    cl=flags_clean(c); mfr=flags_mfr(c)
                    val=cl+(f' →{mfr}' if mfr else '')
            else:
                val=(self.editing if (is_cur and self.editing is not None)
                     else get_field(c,r))
            la=curses.A_BOLD if is_cur else curses.A_NORMAL
            va=curses.color_pair(1) if is_cur else curses.A_NORMAL
            try:
                w.addstr(r+1,1,pad(fn,11),la)
                w.addstr(r+1,13,pad(val,vw)[:vw],va)
                if is_cur and self.editing is not None:
                    w.addstr(r+1,13+min(len(self.editing),vw-1),'_',curses.A_BLINK)
            except: pass

    def _draw_status(self,w,W):
        w.erase()
        if self.prompt_mode==self.PM_NEW_MFR:
            msg=f'New manufacturer name: {self.prompt_buf}_'
            col=curses.color_pair(5)|curses.A_BOLD
            keys='Enter=confirm  ESC=cancel  (valid: A-Z 0-9 SPACE / only)'
        else:
            flag='[UNSAVED] ' if self.dirty else ''
            msg=flag+self.status
            err=any(x in self.status for x in ('✗','ERR','FAIL'))
            col=curses.color_pair(4) if err else curses.color_pair(3)
            keys='TAB=pane  ↑↓  Enter=edit  s=save  a=add chip  n=new mfr  d=del  c=copy  v=verify  q=quit  ►=boundary'
        try:
            w.addstr(0,0,pad(msg,W-1),col|curses.A_BOLD)
            w.addstr(1,0,keys[:W-1],curses.color_pair(2))
        except: pass

    def run(self):
        curses.curs_set(0); self.scr.keypad(True); self.scr.timeout(80)
        while True:
            self.draw()
            k=self.scr.getch()
            if k==-1: continue
            if self.prompt_mode!=self.PM_NONE:
                if not self._prompt_key(k): return
            elif self.editing is not None:
                if not self._edit_key(k): return
            else:
                if not self._nav_key(k): return

    def _prompt_key(self,k):
        if k==27:
            self.prompt_mode=self.PM_NONE; self.prompt_buf=''; self.status='Cancelled'
        elif k in (curses.KEY_BACKSPACE,127,8):
            self.prompt_buf=self.prompt_buf[:-1]
        elif k in (curses.KEY_ENTER,10,13):
            name=valid_name(self.prompt_buf).strip()
            if not name:
                self.status='Name empty or contains no valid cipher characters'
                self.prompt_mode=self.PM_NONE; self.prompt_buf=''; return True
            if self.prompt_mode==self.PM_NEW_MFR:
                s=self.seg; groups=self._groups()
                if s and groups:
                    add_manufacturer_after(s, self.mi, groups, name)
                    self.mi+=1; self.ki=0
                    self.dirty=True
                    self.status=(f'Added manufacturer "{name}" after "{groups[self.mi-1]["name"]}" '
                                 f'— edit chip fields, then s to save')
            self.prompt_mode=self.PM_NONE; self.prompt_buf=''
        elif 32<=k<=126:
            ch=chr(k).upper()
            if ch in VALID_CHARS:
                self.prompt_buf+=ch
        return True

    def _nav_key(self,k):
        s=self.seg; groups=self._groups()
        nc=len(self.segs); ng=len(groups)
        g=self.group; nk=len(g['chip_indices']) if g else 0

        if k in (ord('q'),ord('Q')):
            if self.dirty and not self._qc:
                self.status='Unsaved! Press q again to quit without saving, or s to save.'
                self._qc=True; return True
            return False
        self._qc=False

        if k==9:
            self.pane=(self.pane+1)%4
        elif k==curses.KEY_BTAB:
            self.pane=(self.pane-1)%4
        elif k==curses.KEY_UP:
            if   self.pane==self.P_CAT:   self.ci=clamp(self.ci-1,0,nc-1); self.mi=0; self.ki=0
            elif self.pane==self.P_MFR:   self.mi=clamp(self.mi-1,0,ng-1); self.ki=0
            elif self.pane==self.P_CHIP:  self.ki=clamp(self.ki-1,0,nk-1)
            elif self.pane==self.P_FIELD: self.fi=clamp(self.fi-1,0,len(FIELD_NAMES)-1)
        elif k==curses.KEY_DOWN:
            if   self.pane==self.P_CAT:   self.ci=clamp(self.ci+1,0,nc-1); self.mi=0; self.ki=0
            elif self.pane==self.P_MFR:   self.mi=clamp(self.mi+1,0,ng-1); self.ki=0
            elif self.pane==self.P_CHIP:  self.ki=clamp(self.ki+1,0,nk-1)
            elif self.pane==self.P_FIELD: self.fi=clamp(self.fi+1,0,len(FIELD_NAMES)-1)
        elif k==curses.KEY_PPAGE:
            if   self.pane==self.P_CAT:  self.ci=clamp(self.ci-5,0,nc-1)
            elif self.pane==self.P_MFR:  self.mi=clamp(self.mi-5,0,ng-1)
            elif self.pane==self.P_CHIP: self.ki=clamp(self.ki-10,0,nk-1)
        elif k==curses.KEY_NPAGE:
            if   self.pane==self.P_CAT:  self.ci=clamp(self.ci+5,0,nc-1)
            elif self.pane==self.P_MFR:  self.mi=clamp(self.mi+5,0,ng-1)
            elif self.pane==self.P_CHIP: self.ki=clamp(self.ki+10,0,nk-1)
        elif k in (curses.KEY_RIGHT,curses.KEY_ENTER,10,13):
            if   self.pane==self.P_CAT:  self.pane=self.P_MFR
            elif self.pane==self.P_MFR:  self.pane=self.P_CHIP
            elif self.pane==self.P_CHIP: self.pane=self.P_FIELD
            elif self.pane==self.P_FIELD and self.chip:
                self.editing=(flags_clean(self.chip) if self.fi==9
                              else get_field(self.chip,self.fi))
                curses.curs_set(1)
        elif k in (27,curses.KEY_LEFT):
            if   self.pane==self.P_FIELD: self.pane=self.P_CHIP
            elif self.pane==self.P_CHIP:  self.pane=self.P_MFR
            elif self.pane==self.P_MFR:   self.pane=self.P_CAT
        elif k in (ord('s'),ord('S')):
            try: self._save()
            except Exception as e: self.status=f'ERROR: {e}'
        elif k in (ord('a'),ord('A')):
            if s and groups:
                new_abs=add_chip_to_group(s,self.mi,groups)
                new_groups=get_mfr_groups(s)
                new_g=new_groups[self.mi]
                self.ki=new_g['chip_indices'].index(new_abs)
                self.pane=self.P_FIELD; self.fi=0; self.dirty=True
                self.status=f'New chip added to {groups[self.mi]["name"]} — edit fields, s to save'
        elif k in (ord('n'),ord('N')):
            if s and groups:
                self.prompt_mode=self.PM_NEW_MFR
                self.prompt_buf=''
        elif k in (ord('d'),ord('D')):
            if s and self.chip and self.pane in (self.P_CHIP,self.P_FIELD):
                c=self.chip
                if MFR_SEP in (c[9] if len(c)>9 else b''):
                    self.status='Cannot delete a boundary chip — it anchors the group chain'
                else:
                    name=get_field(c,0)
                    g2=self.group
                    abs_idx=g2['chip_indices'][clamp(self.ki,0,len(g2['chip_indices'])-1)]
                    s['chips'].pop(abs_idx)
                    self.ki=max(0,self.ki-1); self.dirty=True
                    self.status=f'Deleted "{name}"'
        elif k in (ord('c'),ord('C')):
            if s and self.chip:
                c=self.chip; dup=copy.deepcopy(c)
                if MFR_SEP in (dup[9] if len(dup)>9 else b''):
                    dup[9]=_raw_flags_clean(c)  # strip boundary from copy
                set_field(dup,0,get_field(dup,0)+'COPY')
                g2=self.group
                abs_idx=g2['chip_indices'][clamp(self.ki,0,len(g2['chip_indices'])-1)]
                s['chips'].insert(abs_idx+1,dup)
                self.ki+=1; self.dirty=True
                self.status=f'Duplicated as "{get_field(dup,0)}"'
        elif k in (ord('v'),ord('V')):
            self._verify()
        return True

    def _edit_key(self,k):
        if k==27:
            self.editing=None; curses.curs_set(0); self.status='Cancelled'
        elif k in (curses.KEY_ENTER,10,13):
            c=self.chip
            if self.fi==9:
                # Preserve the raw boundary bytes exactly — only re-encode clean part
                old_raw=c[9] if len(c)>9 else b''
                if MFR_SEP in old_raw:
                    old_next_raw=old_raw[old_raw.index(MFR_SEP):]  # includes MFR_SEP + name
                    c[9]=encode_field(self.editing)+old_next_raw
                else:
                    c[9]=encode_field(self.editing)
            else:
                set_field(c,self.fi,self.editing)
            self.editing=None; curses.curs_set(0); self.dirty=True
            self.status=f'{FIELD_NAMES[self.fi]} updated  (s to save)'
            self.fi=clamp(self.fi+1,0,len(FIELD_NAMES)-1)
        elif k in (curses.KEY_BACKSPACE,127,8):
            self.editing=self.editing[:-1]
        elif 32<=k<=126:
            self.editing+=chr(k)
        return True

def main(scr,path):
    App(scr,path).run()

if __name__=='__main__':
    if len(sys.argv)<2:
        print(f'Usage: {sys.argv[0]} database.dat'); sys.exit(1)
    if not os.path.isfile(sys.argv[1]):
        print(f'Not found: {sys.argv[1]}'); sys.exit(1)
    curses.wrapper(main,sys.argv[1])
