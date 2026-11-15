/* SPDX-License-Identifier: MPL-2.0 */
#pragma once
#include <stdint.h>
/* Instrumented builds only. Latest SAVE; a new SAVE replaces it. TIMER4 microseconds.
 * Read pages with the same sequence and flags=0x100 (complete) for a stable record. */
#if FM1B_FLASHTIME
enum { FT_DEST, FT_RESULT, FT_SAVE_US, FT_ERASE_N, FT_ERASE_MAX, FT_ERASE_TOTAL,
       FT_PROGRAM_N, FT_PROGRAM_MAX, FT_PROGRAM_TOTAL, FT_IRQ_MAX, FT_TICKS,
       FT_GAP_MAX, FT_MISSING_EST, FT_ERRORS, FT_ERASE_WAIT_MAX, FT_ERASE_WAIT_TOTAL,
       FT_PROGRAM_WAIT_MAX, FT_PROGRAM_WAIT_TOTAL, FT_FIRST_GAP, FT_POST_GAP,
       FT_IRQ_N, FT_IRQ_TOTAL, FT_SOFT_DROPS, FT_RESERVED1, FT_WORDS };
enum { TK_COUNT, TK_LAST, TK_MAX, TK_TOTAL, TK_GAP_MAX, TK_MISSING_EST,
       TK_SOFT_DROPS, TK_OVER_1000, TK_BIN0, TK_BIN1, TK_BIN2, TK_BIN3,
       TK_BIN4, TK_BIN5, TK_BIN6, TK_BIN7, TK_RACK_FIRST, TK_RACK_LAST,
       TK_EPOCH, TK_SPAN_US, TK_SAVE_TICKS, TK_NO_SAVE_MAX, TK_RESERVED0, TK_RESERVED1,
       TK_WORDS };
void plat_flashtime_tick_reset(void);
void plat_flashtime_tick_end(uint32_t now, uint32_t rack_revision);
void plat_flashtime_begin(unsigned dest);
void plat_flashtime_end(int result);
void plat_flashtime_drop(void);
void plat_flashtime_tick(uint32_t now);
void plat_flashtime_read(unsigned page, uint32_t out[8]);
#endif
