/* Separate DSO: TLSDESC relocation, alignment and >1-page zero-initialized TLS. */
typedef unsigned long usize;
static __thread volatile usize initialized __attribute__((aligned(64))) = 0xabcdef;
static __thread volatile unsigned char padding[5000];
int tls_module_check(usize id, int first) {
    usize address = (usize)&initialized;
    /* Hide the known declaration alignment so this checks the loader at runtime. */
    __asm__ volatile ("" : "+r" (address));
    if ((address & 63) != 0) return 1;
    if (first) {
        if (initialized != 0xabcdef || padding[0] || padding[4999]) return 2;
        initialized = id;
        padding[0] = (unsigned char)id;
        padding[4999] = (unsigned char)(id + 1);
    }
    return initialized != id || padding[0] != id || padding[4999] != id + 1;
}
