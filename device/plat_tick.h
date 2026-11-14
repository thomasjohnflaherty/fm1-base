/* SPDX-License-Identifier: MPL-2.0 */
/* The time to the next app tick (plat_lock_ui_when, plat_tick_due_us), from where TIMER5 is: its count into the period
 * (cnt of prd, 100 us a period), whether an interrupt is pending, and sub, the count that the next interrupt sees (the
 * one that sees 0 runs the app tick, fm1_timer5_irq). The period runs on through a late ISR, so the time comes from the
 * count, not from when the last ISR ran: a pending interrupt runs at once, the ones after it at the period's ends. */
#pragma once
#include <stdint.h>
/* TIMER5's count and pending bit as one snapshot (the interrupts off): the count rolls over between two reads all the same, and
 * a pending bit read before the rollover with the count read after it puts the tick a period late. The count, the bit, the count
 * again: when the count went back, a rollover came between, and the three reads are made again (a period is 100 us) */
struct plat_t5 { uint32_t cnt, pending; };
static inline struct plat_t5 plat_t5_snapshot(uint32_t (*count)(void), uint32_t (*pending)(void))
{
    uint32_t c0, p, c1;
    do {
        c0 = count();
        p = pending();
        c1 = count();
    } while (c1 < c0);
    return (struct plat_t5){c1, p};
}
static inline uint32_t plat_tick_due_model(uint32_t cnt, uint32_t prd, uint32_t pending, uint32_t sub)
{
    uint32_t n = (10u - sub % 10u) % 10u, end;      /* the interrupts after the next one before the tick */
    if (!prd)
        return 0;
    end = (prd - (cnt < prd ? cnt : prd)) * 100u / prd;   /* us to the end of this period */
    if (!pending)
        return end + 100u * n;
    return n ? end + 100u * (n - 1u) : 0u;
}
