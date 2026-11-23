"""Lossless column encoding for browser JSON values. Gzip is applied externally.

The encoder emits UAVC2 byte differences. Both decoders also accept UAVC1 XOR.
Numbers are kept as IEEE-754 values. A column uses float32 only when each number
has exactly the same float64 bit pattern after a float32 round trip. Integers
beyond JavaScript's exact range are rejected. Missing keys and null differ.
"""
import math
import struct

MAGIC = b'UAVC2'
MISSING = object()


def _kind(value):
    if value is MISSING:
        return 0
    if value is None:
        return 1
    if isinstance(value, bool):
        return 2
    if isinstance(value, (int, float)):
        return 3
    if isinstance(value, str):
        return 4
    if isinstance(value, list):
        return 5
    if isinstance(value, dict):
        return 6
    raise TypeError(type(value))


def _number(value):
    if isinstance(value, int) and abs(value) > 2**53:
        raise ValueError('Integer exceeds exact JavaScript range')
    if not math.isfinite(value):
        raise ValueError('Only finite JSON numbers are supported')
    return struct.pack('<d', value)


def encode(value):
    output = bytearray(MAGIC)

    def varint(value):
        while value > 127:
            output.append((value & 127) | 128)
            value >>= 7
        output.append(value)

    def string(value):
        data = value.encode('utf-8', errors='surrogatepass')
        varint(len(data))
        output.extend(data)

    def column(values):
        count = len(values)
        varint(count)
        if count == 0:
            return
        kinds = [_kind(value) for value in values]
        kind = kinds[0]
        if any(k != kind for k in kinds):
            output.append(7)
            output.extend(kinds)
            for item in sorted(set(kinds)):
                column([v for v, k in zip(values, kinds) if k == item])
            return
        output.append(kind)
        if kind < 2:
            return
        if kind == 2:
            for left in range(0, count, 8):
                output.append(sum(int(v) << i for i, v in enumerate(values[left:left+8])))
        elif kind == 3:
            doubles = [_number(v) for v in values]
            if all(d == doubles[0] for d in doubles):
                output.append(0)
                output.extend(doubles[0])
                return
            try:
                singles = [struct.pack('<f', v) for v in values]
                compact = all(struct.pack('<d', struct.unpack('<f', f)[0]) == d
                              for f, d in zip(singles, doubles))
            except (OverflowError, struct.error):
                compact = False
            width = 4 if compact else 8
            output.append(width)
            raw = singles if compact else doubles
            for byte in range(width):
                previous = 0
                for item in raw:
                    output.append((item[byte] - previous) & 255)
                    previous = item[byte]
        elif kind == 4:
            words = list(dict.fromkeys(values))
            lookup = {word: i for i, word in enumerate(words)}
            varint(len(words))
            for word in words:
                string(word)
            if len(words) > 1:
                for value in values:
                    varint(lookup[value])
        elif kind == 5:
            lengths = [len(value) for value in values]
            column(lengths)
            column([item for value in values for item in value])
        elif kind == 6:
            shapes = list(dict.fromkeys(tuple(value) for value in values))
            keys = list(dict.fromkeys(key for shape in shapes for key in shape))
            lookup = {key: i for i, key in enumerate(keys)}
            varint(len(keys))
            for key in keys:
                if not isinstance(key, str):
                    raise TypeError('JSON object keys must be strings')
                string(key)
            varint(len(shapes))
            for shape in shapes:
                varint(len(shape))
                for key in shape:
                    varint(lookup[key])
            if len(shapes) > 1:
                shape_ids = {shape: i for i, shape in enumerate(shapes)}
                for value in values:
                    varint(shape_ids[tuple(value)])
            for key in keys:
                column([value.get(key, MISSING) for value in values])

    column([value])
    return bytes(output)


def decode(data):
    magic = data[:len(MAGIC)]
    if magic not in (b'UAVC1', MAGIC):
        raise ValueError('Invalid column payload')
    additive = magic == MAGIC
    at = len(MAGIC)

    def take(size):
        nonlocal at
        result = data[at:at+size]
        if len(result) != size:
            raise ValueError('Truncated payload')
        at += size
        return result

    def varint():
        value = shift = 0
        while True:
            digit = take(1)[0]
            value |= (digit & 127) << shift
            if digit < 128:
                return value
            shift += 7
            if shift > 49:
                raise ValueError('Invalid varint')

    def string():
        return take(varint()).decode('utf-8', errors='surrogatepass')

    def column():
        count = varint()
        if count == 0:
            return []
        kind = take(1)[0]
        if kind < 2:
            return [MISSING if kind == 0 else None] * count
        if kind == 2:
            bits = take((count+7)//8)
            return [bool(bits[i//8] & (1 << (i % 8))) for i in range(count)]
        if kind == 3:
            width = take(1)[0]
            if width == 0:
                return [struct.unpack('<d', take(8))[0]] * count
            if width not in (4, 8):
                raise ValueError('Invalid float width')
            shuffled = take(width*count)
            raw = bytearray(width*count)
            for byte in range(width):
                previous = 0
                for i in range(count):
                    delta = shuffled[byte*count+i]
                    previous = (previous + delta) & 255 if additive else previous ^ delta
                    raw[i*width+byte] = previous
            code = '<f' if width == 4 else '<d'
            return [value[0] for value in struct.iter_unpack(code, raw)]
        if kind == 4:
            words = [string() for _ in range(varint())]
            return [words[0]] * count if len(words) == 1 else [words[varint()] for _ in range(count)]
        if kind == 5:
            lengths, flat = column(), column()
            result, offset = [], 0
            for length in lengths:
                length = int(length)
                result.append(flat[offset:offset+length])
                offset += length
            if offset != len(flat) or len(result) != count:
                raise ValueError('Invalid array lengths')
            return result
        if kind == 6:
            keys = [string() for _ in range(varint())]
            shapes = [[varint() for _ in range(varint())] for _ in range(varint())]
            ids = [0] * count if len(shapes) == 1 else [varint() for _ in range(count)]
            columns = [column() for _ in keys]
            return [{keys[k]: columns[k][i] for k in shapes[ids[i]]} for i in range(count)]
        if kind == 7:
            kinds = take(count)
            columns = {k: iter(column()) for k in sorted(set(kinds))}
            return [next(columns[k]) for k in kinds]
        raise ValueError('Invalid column type')

    result = column()
    if len(result) != 1 or at != len(data):
        raise ValueError('Invalid payload length')
    return result[0]


JS_DECODER = r'''
window.__LOSSLESS_DECODE__ = function(bytes) {
  let at = 5;
  const text = new TextDecoder('utf-8', {fatal:true});
  const missing = Symbol('missing');
  const magic = text.decode(bytes.subarray(0,5));
  if (magic !== 'UAVC1' && magic !== 'UAVC2') throw new Error('Unknown packed format');
  const additive = magic === 'UAVC2';
  function take(size) {
    if (at+size > bytes.length) throw new Error('Truncated packed data');
    const result=bytes.subarray(at,at+size);at+=size;return result;
  }
  function integer() {
    let result=0,factor=1;
    for(let i=0;i<8;i++) {const digit=take(1)[0];result+=(digit&127)*factor;if(digit<128)return result;factor*=128;}
    throw new Error('Invalid packed integer');
  }
  const string=()=>text.decode(take(integer()));
  function column() {
    const count=integer();if(!count)return [];
    const kind=take(1)[0];
    if(kind<2)return Array(count).fill(kind===0?missing:null);
    if(kind===2){const bits=take(Math.ceil(count/8));return Array.from({length:count},(_,i)=>!!(bits[i>>3]&(1<<(i&7))));}
    if(kind===3){
      const width=take(1)[0];
      if(width===0){const a=take(8);return Array(count).fill(new DataView(a.buffer,a.byteOffset,8).getFloat64(0,true));}
      if(width!==4&&width!==8)throw new Error('Invalid packed number');
      const shuffled=take(width*count),raw=new Uint8Array(width*count);
      for(let byte=0;byte<width;byte++){let previous=0;for(let i=0;i<count;i++){previous=additive?(previous+shuffled[byte*count+i])&255:previous^shuffled[byte*count+i];raw[i*width+byte]=previous;}}
      const view=new DataView(raw.buffer),values=Array(count);
      for(let i=0;i<count;i++)values[i]=width===4?view.getFloat32(i*width,true):view.getFloat64(i*width,true);
      return values;
    }
    if(kind===4){const words=Array.from({length:integer()},string);return words.length===1?Array(count).fill(words[0]):Array.from({length:count},()=>words[integer()]);}
    if(kind===5){
      const lengths=column(),flat=column();let offset=0;
      const result=lengths.map(length=>{const value=flat.slice(offset,offset+length);offset+=length;return value;});
      if(result.length!==count||offset!==flat.length)throw new Error('Invalid packed array');return result;
    }
    if(kind===6){
      const keys=Array.from({length:integer()},string);
      const shapes=Array.from({length:integer()},()=>Array.from({length:integer()},integer));
      const indices=shapes.length===1?Array(count).fill(0):Array.from({length:count},integer);
      const fields=keys.map(()=>column());
      return indices.map((shape,i)=>Object.fromEntries(shapes[shape].map(k=>[keys[k],fields[k][i]])));
    }
    if(kind===7){
      const kinds=take(count),columns=new Map(),indices=new Map();
      for(const k of [...new Set(kinds)].sort((a,b)=>a-b)){columns.set(k,column());indices.set(k,0);}
      return Array.from(kinds,k=>{const index=indices.get(k);indices.set(k,index+1);return columns.get(k)[index];});
    }
    throw new Error('Invalid packed column');
  }
  const result=column();if(result.length!==1||at!==bytes.length)throw new Error('Invalid packed length');return result[0];
};
'''
