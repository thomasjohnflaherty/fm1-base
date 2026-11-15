/* SPDX-License-Identifier: MPL-2.0 */
/* Included after fm1_irq.h, before the other drivers. Observe software cli/sti calls:
 * these are physical enable transitions, not plat_lock depth. Never change IRQ policy.
 * No logging or measurement code executes with XIP off. */
#include "plat_flashtime.h"
static volatile uint32_t ft_words[FT_WORDS], ft_seq, ft_active, ft_pending;
static uint32_t ft_started, ft_tick_last, ft_irq_started;
static uint8_t ft_irq_open, ft_first;
static volatile uint32_t tk_words[TK_WORDS];
static uint32_t tk_snapshot[TK_WORDS], tk_snapshot_seq;
static uint32_t tk_started, tk_epoch_started;
static uint8_t tk_save_overlap;
static void tk_max(unsigned at, uint32_t v) { if (v > tk_words[at]) tk_words[at] = v; }
void plat_flashtime_tick_reset(void)
{
    /* Caller holds plat_lock (the existing command 46 op 4). */
    uint32_t epoch = tk_words[TK_EPOCH] + 1;
    for (unsigned i = 0; i < TK_WORDS; ++i) tk_words[i] = 0;
    tk_words[TK_EPOCH] = epoch; tk_epoch_started = fm1_ticks(); ft_tick_last = 0;
}
void plat_flashtime_tick_end(uint32_t now, uint32_t rack_revision)
{
    uint32_t us = (now - tk_started) / FM1_TICKS_PER_US;
    if (!tk_words[TK_COUNT]) tk_words[TK_RACK_FIRST] = rack_revision;
    tk_words[TK_RACK_LAST] = rack_revision;
    ++tk_words[TK_COUNT]; tk_words[TK_LAST] = us; tk_max(TK_MAX, us); tk_words[TK_TOTAL] += us;
    tk_words[TK_OVER_1000] += us > 1000;
    unsigned bin = us <= 100 ? 0 : us <= 250 ? 1 : us <= 500 ? 2 : us <= 750 ? 3 :
                   us <= 1000 ? 4 : us <= 1500 ? 5 : us <= 2000 ? 6 : 7;
    ++tk_words[TK_BIN0 + bin];
    if (tk_save_overlap) ++tk_words[TK_SAVE_TICKS]; else tk_max(TK_NO_SAVE_MAX, us);
}
static void ft_max(unsigned at, uint32_t v) { if (v > ft_words[at]) ft_words[at] = v; }
static void ft_off(void)
{
    fm1_irq_off();
    if (ft_active && !ft_irq_open) { ft_irq_started = fm1_ticks(); ft_irq_open = 1; }
}
static void ft_irq_finish(void)
{
    if (ft_active && ft_irq_open) {
        uint32_t us = (fm1_ticks() - ft_irq_started) / FM1_TICKS_PER_US;
        ft_max(FT_IRQ_MAX, us); ++ft_words[FT_IRQ_N]; ft_words[FT_IRQ_TOTAL] += us;
        ft_irq_open = 0;
    }
}
static void ft_on(void)
{
    ft_irq_finish();
    fm1_irq_on();
}
/* Functions above call the original HAL. Later includes call these wrappers. */
#define fm1_irq_off ft_off
#define fm1_irq_on ft_on
void plat_flashtime_begin(unsigned dest)
{
    fm1_irq_off();
    ft_active = ft_pending = 0;
    for (unsigned i = 0; i < FT_WORDS; ++i) ft_words[i] = 0;
    ++ft_seq; ft_words[FT_DEST] = dest;
    ft_started = fm1_ticks(); ft_first = 1; ft_irq_open = 0;
    ft_active = 1;
    fm1_irq_on();
}
void plat_flashtime_end(int result)
{
    fm1_irq_off();
    ft_words[FT_RESULT] = (uint32_t)result;
    ft_words[FT_SAVE_US] = (fm1_ticks() - ft_started) / FM1_TICKS_PER_US;
    /* Close atomically: the first tick after SAVE must see pending. */
    ft_irq_finish();
    ft_active = 0; ft_pending = 1;
    fm1_irq_on();
}
void plat_flashtime_tick(uint32_t now)
{
    tk_started = now; tk_save_overlap = ft_active || ft_pending;
    if (ft_tick_last) {
        uint32_t us = (now - ft_tick_last) / FM1_TICKS_PER_US;
        tk_max(TK_GAP_MAX, us);
        if (us >= 2000) tk_words[TK_MISSING_EST] += us / 1000 - 1;
    }
    if ((ft_active || ft_pending) && ft_tick_last) {
        uint32_t us = (now - ft_tick_last) / FM1_TICKS_PER_US;
        ++ft_words[FT_TICKS]; ft_max(FT_GAP_MAX, us);
        /* Estimate unserved 1 kHz opportunities; jitter is not a hardware drop count. */
        if (us >= 2000) ft_words[FT_MISSING_EST] += us / 1000 - 1;
        if (ft_first) { ft_words[FT_FIRST_GAP] = us; ft_first = 0; }
        if (ft_pending) { ft_words[FT_POST_GAP] = us; ft_pending = 0; }
    }
    ft_tick_last = now;
}
void plat_flashtime_drop(void)
{
    if (ft_active || ft_pending) ++ft_words[FT_SOFT_DROPS];
    ++tk_words[TK_SOFT_DROPS];
}
void plat_flashtime_read(unsigned page, uint32_t out[8])
{
    uint32_t lock = plat_lock();
    if (page >= 4) {
        /* Page 4 freezes a coherent readout, without resetting live counters. */
        if (page == 4) {
            tk_words[TK_SPAN_US] = (fm1_ticks() - tk_epoch_started) / FM1_TICKS_PER_US;
            for (unsigned i = 0; i < TK_WORDS; ++i) tk_snapshot[i] = tk_words[i];
            ++tk_snapshot_seq;
        }
        out[0] = tk_snapshot_seq; out[1] = 0x200;
        for (unsigned i = 0; i < 6; ++i) out[i+2] = tk_snapshot[(page-4)*6+i];
        plat_unlock(lock);
        return;
    }
    out[0] = ft_seq;
    out[1] = 0x100u | (ft_active ? 1u : 0u) | (ft_pending ? 2u : 0u);
    for (unsigned i = 0; i < 6; ++i) out[i+2] = ft_words[page*6+i];
    plat_unlock(lock);
}
static void ft_flash(unsigned base, uint32_t ticks, uint32_t wait, int rc)
{
    if (!ft_active) return;
    uint32_t us = ticks / FM1_TICKS_PER_US;
    ++ft_words[base]; ft_max(base+1, us); ft_words[base+2] += us;
    unsigned w = base == FT_ERASE_N ? FT_ERASE_WAIT_MAX : FT_PROGRAM_WAIT_MAX;
    ft_max(w, wait); ft_words[w+1] += wait;
    ft_words[FT_ERRORS] += rc != 0;
}
