/**
 * Synchronous SHA-1.
 *
 * The job/record fingerprint must be byte-identical to Python's
 * `hashlib.sha1(...).hexdigest()` (see src/job_scraper/models.py) because both
 * halves of HarvestKit write into the same `id` column. `crypto.subtle.digest`
 * is async and would force every call site — including Dexie `put()` paths and
 * pure render helpers — to become async, so we hash inline instead.
 *
 * Handles the full Unicode range by UTF-8 encoding first, matching Python's
 * `.encode("utf-8")`.
 */

function utf8Bytes(input: string): Uint8Array {
  if (typeof TextEncoder !== "undefined") return new TextEncoder().encode(input);
  // TextEncoder is present in every MV3 context; this is belt-and-braces.
  const out: number[] = [];
  for (let i = 0; i < input.length; i++) {
    let c = input.charCodeAt(i);
    if (c < 0x80) out.push(c);
    else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 0x3f));
    else if (c >= 0xd800 && c <= 0xdbff && i + 1 < input.length) {
      c = 0x10000 + ((c - 0xd800) << 10) + (input.charCodeAt(++i) - 0xdc00);
      out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 0x3f), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
    } else out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
  }
  return new Uint8Array(out);
}

const rotl = (n: number, s: number) => ((n << s) | (n >>> (32 - s))) >>> 0;

export function sha1Hex(input: string): string {
  const bytes = utf8Bytes(input);
  const bitLen = bytes.length * 8;

  // Pad to a multiple of 64 bytes: 0x80, zeros, then a 64-bit big-endian length.
  const padded = new Uint8Array(((bytes.length + 8) >> 6) * 64 + 64);
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 8, Math.floor(bitLen / 0x100000000), false);
  view.setUint32(padded.length - 4, bitLen >>> 0, false);

  let h0 = 0x67452301;
  let h1 = 0xefcdab89;
  let h2 = 0x98badcfe;
  let h3 = 0x10325476;
  let h4 = 0xc3d2e1f0;
  const w = new Uint32Array(80);

  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(offset + i * 4, false);
    for (let i = 16; i < 80; i++) w[i] = rotl(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);

    let a = h0;
    let b = h1;
    let c = h2;
    let d = h3;
    let e = h4;

    for (let i = 0; i < 80; i++) {
      let f: number;
      let k: number;
      if (i < 20) {
        f = (b & c) | (~b & d);
        k = 0x5a827999;
      } else if (i < 40) {
        f = b ^ c ^ d;
        k = 0x6ed9eba1;
      } else if (i < 60) {
        f = (b & c) | (b & d) | (c & d);
        k = 0x8f1bbcdc;
      } else {
        f = b ^ c ^ d;
        k = 0xca62c1d6;
      }
      const temp = (rotl(a, 5) + f + e + k + w[i]) >>> 0;
      e = d;
      d = c;
      c = rotl(b, 30);
      b = a;
      a = temp;
    }

    h0 = (h0 + a) >>> 0;
    h1 = (h1 + b) >>> 0;
    h2 = (h2 + c) >>> 0;
    h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0;
  }

  return [h0, h1, h2, h3, h4].map((n) => n.toString(16).padStart(8, "0")).join("");
}
