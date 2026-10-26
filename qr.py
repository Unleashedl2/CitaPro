"""Generador mínimo de códigos QR (modo byte, corrección M, versiones 1-10). Sin dependencias."""
_EC = {1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
       6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]), 10: (26, [(4, 43), (1, 44)])}
_AL = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
_EXP, _LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x; _LOG[_x] = _i; _x <<= 1
    if _x & 0x100: _x ^= 0x11D
for _i in range(255, 512): _EXP[_i] = _EXP[_i - 255]

def _mul(a, b): return 0 if not a or not b else _EXP[_LOG[a] + _LOG[b]]
def _rs(data, n):
    g = [1]
    for i in range(n):
        ng = [0] * (len(g) + 1)
        for j, c in enumerate(g): ng[j] ^= c; ng[j + 1] ^= _mul(c, _EXP[i])
        g = ng
    res = [0] * n
    for b in data:
        f = b ^ res[0]; res = res[1:] + [0]
        for i in range(n): res[i] ^= _mul(g[i + 1], f)
    return res

def make(text):
    data = text.encode('utf-8')
    for ver in range(1, 11):
        ec, groups = _EC[ver]; total = sum(n * d for n, d in groups)
        if len(data) <= total - (2 if ver < 10 else 3): break
    else: raise ValueError('Texto demasiado largo para el QR')
    bits = []
    def put(v, n): bits.extend((v >> (n - 1 - i)) & 1 for i in range(n))
    put(4, 4); put(len(data), 8 if ver < 10 else 16)
    for b in data: put(b, 8)
    put(0, min(4, total * 8 - len(bits)))
    while len(bits) % 8: bits.append(0)
    cw = [int(''.join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    k = 0
    while len(cw) < total: cw.append((0xEC, 0x11)[k % 2]); k += 1
    blocks, pos = [], 0
    for n, d in groups:
        for _ in range(n): blocks.append(cw[pos:pos + d]); pos += d
    ecs = [_rs(b, ec) for b in blocks]; out = []
    for i in range(max(map(len, blocks))):
        out += [b[i] for b in blocks if i < len(b)]
    for i in range(ec): out += [e[i] for e in ecs]
    return _matrix(ver, out)

def _matrix(ver, cws):
    n = ver * 4 + 17
    m = [[False] * n for _ in range(n)]; fn = [[False] * n for _ in range(n)]
    def st(x, y, v):
        if 0 <= x < n and 0 <= y < n: m[y][x] = v; fn[y][x] = True
    for i in range(n): st(6, i, i % 2 == 0); st(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5): st(cx + dx, cy + dy, max(abs(dx), abs(dy)) not in (2, 4))
    a = _AL[ver]
    for i, ax in enumerate(a):
        for j, ay in enumerate(a):
            if (i == 0 and j == 0) or (i == 0 and j == len(a) - 1) or (i == len(a) - 1 and j == 0): continue
            for dy in range(-2, 3):
                for dx in range(-2, 3): st(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
    def fmt(mask):
        r = mask
        for _ in range(10): r = (r << 1) ^ ((r >> 9) * 0x537)
        bits = ((mask << 10) | r) ^ 0x5412; g = lambda i: (bits >> i) & 1 == 1
        for i in range(6): st(8, i, g(i))
        st(8, 7, g(6)); st(8, 8, g(7)); st(7, 8, g(8))
        for i in range(9, 15): st(14 - i, 8, g(i))
        for i in range(8): st(n - 1 - i, 8, g(i))
        for i in range(8, 15): st(8, n - 15 + i, g(i))
        st(8, n - 8, True)
    fmt(0)
    if ver >= 7:
        r = ver
        for _ in range(12): r = (r << 1) ^ ((r >> 11) * 0x1F25)
        bits = (ver << 12) | r
        for i in range(18):
            v = (bits >> i) & 1 == 1; st(n - 11 + i % 3, i // 3, v); st(i // 3, n - 11 + i % 3, v)
    i = 0; right = n - 1
    while right >= 1:
        if right == 6: right = 5
        for vert in range(n):
            for j in range(2):
                x = right - j; y = n - 1 - vert if ((right + 1) & 2) == 0 else vert
                if not fn[y][x] and i < len(cws) * 8:
                    m[y][x] = (cws[i >> 3] >> (7 - (i & 7))) & 1 == 1; i += 1
        right -= 2
    conds = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0, lambda x, y: (x + y) % 3 == 0,
             lambda x, y: (x // 3 + y // 2) % 2 == 0, lambda x, y: x * y % 2 + x * y % 3 == 0,
             lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0, lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0]
    def mask(k):
        for y in range(n):
            for x in range(n):
                if not fn[y][x] and conds[k](x, y): m[y][x] = not m[y][x]
    best, bp = 0, None
    for k in range(8):
        mask(k); fmt(k); p = _pen(m)
        if bp is None or p < bp: best, bp = k, p
        mask(k)
    mask(best); fmt(best)
    return m

def _pen(m):
    n = len(m); p = 0
    for rows in (m, [list(c) for c in zip(*m)]):
        for r in rows:
            run = 1
            for i in range(1, n):
                if r[i] == r[i - 1]: run += 1
                else:
                    if run >= 5: p += run - 2
                    run = 1
            if run >= 5: p += run - 2
            s = ''.join('1' if v else '0' for v in r); p += 40 * (s.count('10111010000') + s.count('00001011101'))
    for y in range(n - 1):
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]: p += 3
    return p + 10 * int(abs(sum(map(sum, m)) * 100 / (n * n) - 50) // 5)

def svg(text, border=4, scale=8):
    m = make(text); n = len(m); s = n + 2 * border
    d = ''.join(f'M{x + border},{y + border}h1v1h-1z' for y in range(n) for x in range(n) if m[y][x])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {s} {s}" width="{s * scale}" height="{s * scale}" '
            f'shape-rendering="crispEdges"><rect width="{s}" height="{s}" fill="#fff"/><path d="{d}" fill="#000"/></svg>')
