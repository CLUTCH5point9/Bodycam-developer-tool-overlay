"""UE5.5 GVAS property walker for Bodycam Loadout.sav.

Layout discovered from the file itself:
    Property := Name:FString  TypeName  Size:int32  HasGuid:u8  Payload[Size]
    TypeName := Name:FString  ParamCount:int32  ParamCount * TypeName   (recursive)

Knowing where each Size field lives lets us change a string's length and fix up
every enclosing property's Size by the same delta. Format background and why
this is a raw binary patch rather than a re-serialization: see
1-DOCUMENTATION.md section 5.2.
"""
import struct

def rd_str(d, i):
    (n,) = struct.unpack_from('<i', d, i)
    if n < 0 or n > 4096 or i + 4 + n > len(d):
        raise ValueError(f"bad string len {n} at {i}")
    return bytes(d[i+4:i+4+n-1]).decode('ascii', 'replace'), i + 4 + n

def rd_typename(d, i):
    name, i = rd_str(d, i)
    (cnt,) = struct.unpack_from('<i', d, i); i += 4
    params = []
    for _ in range(cnt):
        p, i = rd_typename(d, i)
        params.append(p)
    return (name, params), i

def walk(d, i, end, out, depth=0):
    """Walk a property list until 'None' or `end`; append region records."""
    while i < end:
        try:
            name, j = rd_str(d, i)
        except ValueError:
            return
        if name == 'None':
            return
        try:
            (tname, params), j = rd_typename(d, j)
        except ValueError:
            return
        if j + 5 > len(d):
            return
        (size,) = struct.unpack_from('<i', d, j)
        size_off = j
        j += 4
        j += 1                      # HasPropertyGuid
        payload, pend = j, j + size
        if size < 0 or pend > len(d):
            return
        out.append(dict(name=name, type=tname, params=params, depth=depth,
                        size_off=size_off, size=size, start=payload, end=pend))
        # descend into containers so nested Size fields are recorded too
        if tname in ('StructProperty', 'ArrayProperty', 'MapProperty'):
            scan_inner(d, payload, pend, out, depth + 1)
        i = pend

def scan_inner(d, start, end, out, depth):
    """Find nested property lists inside a container payload."""
    i = start
    while i < end - 8:
        try:
            name, j = rd_str(d, i)
            if name and name != 'None':
                (tn, _p), j2 = rd_typename(d, j)
                if tn.endswith('Property') and j2 + 5 <= len(d):
                    (size,) = struct.unpack_from('<i', d, j2)
                    pay = j2 + 5
                    if 0 <= size and pay + size <= end:
                        out.append(dict(name=name, type=tn, params=_p, depth=depth,
                                        size_off=j2, size=size, start=pay, end=pay+size))
                        if tn in ('StructProperty','ArrayProperty','MapProperty'):
                            scan_inner(d, pay, pay+size, out, depth+1)
                        i = pay + size
                        continue
        except (ValueError, struct.error):
            pass
        i += 1

def regions(path):
    d = bytearray(open(path,'rb').read())
    out = []
    # skip the GVAS header: find the SaveGame class name, then properties follow
    tag = b'/Game/GM/SaveGame/SG_Loadout.SG_Loadout_C'
    anchor = d.find(tag)
    start = anchor + len(tag) + 2   # NUL terminator + one padding byte
    walk(d, start, len(d), out)
    return d, out

def rowname_regions(path):
    """Every RowName NameProperty, with the FString offset and current value."""
    d, regs = regions(path)
    out = []
    for r in regs:
        if r['name'] == 'RowName' and r['type'] == 'NameProperty':
            s, _ = rd_str(d, r['start'])
            out.append(dict(str_off=r['start'], value=s, region=r))
    return d, regs, out

def set_rowname(path, str_off, expected, new):
    """Replace a RowName FString, fixing Size on every enclosing property."""
    d, regs = regions(path)
    cur, after = rd_str(d, str_off)
    assert cur == expected, f"at {str_off}: found {cur!r}, expected {expected!r}"
    delta = len(new) - len(cur)
    if delta:
        for r in regs:
            if r['start'] <= str_off < r['end']:
                struct.pack_into('<i', d, r['size_off'], r['size'] + delta)
    payload = struct.pack('<i', len(new)+1) + new.encode('ascii') + b'\x00'
    d[str_off:after] = payload
    open(path,'wb').write(d)
    return delta

# ---------------------------------------------------------------- slot helpers
def slots(path):
    """Return (d, regs, rows, loadouts) where loadouts[i] = dict(op=region, slots=[...])
    and each slot = dict(bundle=region, weapon=region, attachments=region|None)."""
    d, regs, rows = rowname_regions(path)
    wls = sorted([r for r in regs if r['name'].startswith('WeaponsList')], key=lambda r: r['start'])
    ops = sorted([r for r in regs if r['name'].startswith('OperatorSkin')], key=lambda r: r['start'])
    loadouts = []
    for i, wl in enumerate(wls):
        inner = sorted([x for x in regs if wl['start'] <= x['start'] and x['end'] <= wl['end']],
                       key=lambda x: x['start'])
        sl, cur = [], None
        for r in inner:
            if r['name'].startswith('BasicParentBundle'):
                cur = dict(bundle=r, weapon=None, attachments=None); sl.append(cur)
            elif cur is not None and r['name'].startswith('Wep_'):
                cur['weapon'] = r
            elif cur is not None and r['name'].startswith('Attachments') and cur['attachments'] is None:
                cur['attachments'] = r
        loadouts.append(dict(op=ops[i], slots=sl))
    return d, regs, rows, loadouts

def row_in(rows, region):
    ks = [k for k in rows if region['start'] <= k['str_off'] < region['end']]
    return ks

def set_row_in_region(path, region_getter, new):
    """Resolve a region fresh, then rewrite the single RowName inside it."""
    d, regs, rows, L = slots(path)
    region = region_getter(L)
    ks = row_in(rows, region)
    assert len(ks) == 1, f"expected 1 RowName in region, found {len(ks)}"
    return set_rowname(path, ks[0]['str_off'], ks[0]['value'], new)

def replace_payload(path, region_getter, payload):
    """Replace a region's whole payload (e.g. an Attachments array) and fix sizes."""
    d, regs, rows, L = slots(path)
    tgt = region_getter(L)
    start, end = tgt['start'], tgt['end']
    delta = len(payload) - (end - start)
    for r in regs:
        if r is tgt:
            struct.pack_into('<i', d, r['size_off'], len(payload))
        elif r['start'] <= start and r['end'] >= end and r['size_off'] < start:
            (sz,) = struct.unpack_from('<i', d, r['size_off'])
            struct.pack_into('<i', d, r['size_off'], sz + delta)
    d[start:end] = payload
    open(path, 'wb').write(d)
    return delta

def payload_of(path, region_getter):
    d, regs, rows, L = slots(path)
    r = region_getter(L)
    return bytes(d[r['start']:r['end']])

def set_index(path, n):
    """Set SavedCurrentLoadoutIndex, INSERTING the property if the game omitted it (it drops
    properties equal to their default, so index 0 disappears from the file)."""
    d, regs = regions(path)
    d = bytearray(d)
    idx = [r for r in regs if r['name'] == 'SavedCurrentLoadoutIndex']
    if idx:
        struct.pack_into('<i', d, idx[0]['start'], n)
    else:
        tag = b'/Game/GM/SaveGame/SG_Loadout.SG_Loadout_C'
        start = d.find(tag) + len(tag) + 2          # NUL + padding byte -> first property
        def fstr(s): b = s.encode('ascii') + b'\x00'; return struct.pack('<i', len(b)) + b
        prop = (fstr('SavedCurrentLoadoutIndex') + fstr('IntProperty') + struct.pack('<i', 0)
                + struct.pack('<i', 4) + b'\x00' + struct.pack('<i', n))
        d[start:start] = prop
    open(path, 'wb').write(d)
    d2, regs2 = regions(path)
    r = [x for x in regs2 if x['name'] == 'SavedCurrentLoadoutIndex'][0]
    return struct.unpack_from('<i', d2, r['start'])[0]
