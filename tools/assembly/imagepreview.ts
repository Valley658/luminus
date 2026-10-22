let heapOffset: usize = 65536;

export function alloc(size: i32): usize {
  const ptr = heapOffset;
  const needed = ptr + <usize>size;
  const currentBytes = <usize>memory.size() * 65536;
  if (needed > currentBytes) {
    const growBy = <i32>((needed - currentBytes + 65535) / 65536);
    memory.grow(growBy);
  }
  heapOffset = needed;
  return ptr;
}

export function reset(): void {
  heapOffset = 65536;
}

export function resizeBilinear(srcPtr: usize, srcW: i32, srcH: i32, dstW: i32, dstH: i32): usize {
  const dstSize = dstW * dstH * 4;
  const dstPtr = alloc(dstSize);
  const xRatio = <f64>srcW / <f64>dstW;
  const yRatio = <f64>srcH / <f64>dstH;

  for (let y = 0; y < dstH; y++) {
    const srcYf = (<f64>y + 0.5) * yRatio - 0.5;
    let y0 = <i32>Math.floor(srcYf);
    let yFrac = srcYf - <f64>y0;
    if (y0 < 0) {
      y0 = 0;
      yFrac = 0;
    }
    let y1 = y0 + 1;
    if (y1 >= srcH) y1 = srcH - 1;

    for (let x = 0; x < dstW; x++) {
      const srcXf = (<f64>x + 0.5) * xRatio - 0.5;
      let x0 = <i32>Math.floor(srcXf);
      let xFrac = srcXf - <f64>x0;
      if (x0 < 0) {
        x0 = 0;
        xFrac = 0;
      }
      let x1 = x0 + 1;
      if (x1 >= srcW) x1 = srcW - 1;

      for (let c = 0; c < 4; c++) {
        const p00 = <f64>load<u8>(srcPtr + <usize>((y0 * srcW + x0) * 4 + c));
        const p10 = <f64>load<u8>(srcPtr + <usize>((y0 * srcW + x1) * 4 + c));
        const p01 = <f64>load<u8>(srcPtr + <usize>((y1 * srcW + x0) * 4 + c));
        const p11 = <f64>load<u8>(srcPtr + <usize>((y1 * srcW + x1) * 4 + c));
        const top = p00 + (p10 - p00) * xFrac;
        const bottom = p01 + (p11 - p01) * xFrac;
        const value = top + (bottom - top) * yFrac;
        let v = <i32>(value + 0.5);
        if (v < 0) v = 0;
        if (v > 255) v = 255;
        store<u8>(dstPtr + <usize>((y * dstW + x) * 4 + c), <u8>v);
      }
    }
  }

  return dstPtr;
}
