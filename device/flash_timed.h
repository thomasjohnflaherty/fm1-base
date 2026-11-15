/* SPDX-License-Identifier: MPL-2.0 */
/* Reuse the pinned RAM driver unchanged. Replace only the XIP-side wrappers so
 * each page exposes its existing took_us result; no BSP gitlink change. */
#define fl_erase4k fl_erase4k_unmeasured
#define fl_write fl_write_unmeasured
#include "fm1_flash.h"
#undef fl_erase4k
#undef fl_write
static int fl_erase4k(uint32_t off, uint32_t *took_us)
{
    *took_us = 0; /* early driver errors do not set it */
    fm1_irq_off();
    uint32_t t = fm1_ticks();
    int rc = FL_FAR(fl_erase4k_ram)(off, took_us);
    t = fm1_ticks() - t;
    ft_flash(FT_ERASE_N, t, *took_us, rc);
    fm1_irq_on();
    return rc;
}
static int fl_write(uint32_t off, const uint8_t *src_ram, uint32_t n)
{
    while (n) {
        uint32_t chunk = 256u - (off & 0xFFu), took = 0;
        if (chunk > n) chunk = n;
        fm1_irq_off();
        uint32_t t = fm1_ticks();
        int rc = FL_FAR(fl_prog_ram)(off, src_ram, chunk, &took);
        t = fm1_ticks() - t;
        ft_flash(FT_PROGRAM_N, t, took, rc);
        fm1_irq_on();
        if (rc) return rc;
        off += chunk; src_ram += chunk; n -= chunk;
    }
    return 0;
}
