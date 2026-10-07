/**
 * Node preload that moves `Date` forward `SHIFT_DAYS` days, for vitest's workers through
 * `NODE_OPTIONS=--import`. See docs/reference/commands.md#clock-check.
 */
const offset = Number(process.env.SHIFT_DAYS || 0) * 86_400_000
const RealDate = globalThis.Date

class ShiftedDate extends RealDate {
  constructor(...args) {
    if (args.length === 0) super(RealDate.now() + offset)
    else super(...args)
  }

  static now() {
    return RealDate.now() + offset
  }
}

globalThis.Date = ShiftedDate
