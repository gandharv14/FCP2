/* Freestanding Linux AMD64 downward set-ID transition. No libc or loader.
 * Compile: clang --target=x86_64-unknown-linux-gnu -ffreestanding -fno-builtin
 *   -fno-stack-protector -fno-pic -nostdinc -O2 -c recursion_demote_exec.c
 * Link as static ELF with entry _start; see build_recursion_demote_helper.py.
 * The trusted parent owns the template; private copy is 60100:60100 mode6755
 * inside root:root mode0700 directory. Parent drops bounding caps BEFORE exec.
 */
#if !defined(__linux__) || !defined(__x86_64__)
#error This helper is specifically for Linux AMD64.
#endif

typedef unsigned int u32;
typedef unsigned long u64;
#define SOLVER_ID 60100
#define SYS_write 1
#define SYS_execve 59
#define SYS_exit 60
#define SYS_getuid 102
#define SYS_getgid 104
#define SYS_geteuid 107
#define SYS_getegid 108
#define SYS_getgroups 115
#define SYS_setresuid 117
#define SYS_getresuid 118
#define SYS_setresgid 119
#define SYS_getresgid 120
#define SYS_capget 125
#define SYS_capset 126
#define SYS_prctl 157
#define SYS_setrlimit 160
#define PR_SET_DUMPABLE 4
#define PR_CAPBSET_READ 23
#define PR_SET_NO_NEW_PRIVS 38
#define PR_GET_NO_NEW_PRIVS 39
#define PR_CAP_AMBIENT 47
#define PR_CAP_AMBIENT_IS_SET 1
#define RLIMIT_CORE 4
#define EPERM 1
#define EINVAL 22

static long kernel(long number, long a, long b, long c, long d, long e, long f) {
    long result;
    register long r10 __asm__("r10") = d;
    register long r8 __asm__("r8") = e;
    register long r9 __asm__("r9") = f;
    __asm__ volatile("syscall" : "=a"(result) : "a"(number), "D"(a), "S"(b),
                     "d"(c), "r"(r10), "r"(r8), "r"(r9) : "rcx", "r11", "memory");
    return result;
}
#define S0(n) kernel((n),0,0,0,0,0,0)
#define S2(n,a,b) kernel((n),(long)(a),(long)(b),0,0,0,0)
#define S3(n,a,b,c) kernel((n),(long)(a),(long)(b),(long)(c),0,0,0)
#define P2(option,arg) kernel(SYS_prctl,(option),(arg),0,0,0,0)

static char *append(char *target, const char *value) {
    while (*value) *target++ = *value++;
    return target;
}
static char *decimal(char *target, u64 value) {
    char digits[24];
    int size = 0;
    do { digits[size++] = (char)('0' + value % 10); value /= 10; } while (value);
    while (size) *target++ = digits[--size];
    return target;
}
__attribute__((noreturn)) static void fail(const char *stage, long error) {
    char message[384];
    char *end = append(message, "FCP_DEMOTION_FAILED stage=");
    end = append(end, stage);
    end = append(end, " errno="); end = decimal(end, error < 0 ? (u64)-error : (u64)error);
    end = append(end, " uid="); end = decimal(end, (u64)S0(SYS_getuid));
    end = append(end, " euid="); end = decimal(end, (u64)S0(SYS_geteuid));
    end = append(end, " gid="); end = decimal(end, (u64)S0(SYS_getgid));
    end = append(end, " egid="); end = decimal(end, (u64)S0(SYS_getegid));
    *end++ = '\n';
    S3(SYS_write, 2, message, end - message);
    kernel(SYS_exit, 125, 0, 0, 0, 0, 0);
    __builtin_unreachable();
}
static void require(long result, const char *stage) {
    if (result < 0) fail(stage, result);
}

__attribute__((noreturn, used)) void demote_main(long *stack) {
    long argc = stack[0];
    char **argv = (char **)(stack + 1);
    char **environment = argv + argc + 1;
    u32 real_uid, effective_uid, saved_uid, real_gid, effective_gid, saved_gid;
    struct { u32 version; int pid; } header = {0x20080522U, 0};
    struct { u32 effective, permitted, inheritable; } capabilities[2] = {{0,0,0},{0,0,0}};
    struct { u64 current, maximum; } core_limit = {0,0};
    u32 groups[64];
    long count, value;

    if (argc < 2 || argv[1][0] != '/') fail("absolute_command_required", -EINVAL);
    /* Set-ID ignored because of nosuid/NNP: stop before running any model. */
    if (S0(SYS_geteuid) != SOLVER_ID || S0(SYS_getegid) != SOLVER_ID)
        fail("setid_file_transition_not_applied", -EPERM);
    /* Each requested ID is already effective. These downward changes need no
     * SETUID/SETGID capabilities, and erase all real/saved root IDs. */
    require(S3(SYS_setresgid, SOLVER_ID, SOLVER_ID, SOLVER_ID), "setresgid");
    require(S3(SYS_setresuid, SOLVER_ID, SOLVER_ID, SOLVER_ID), "setresuid");
    require(S3(SYS_getresuid, &real_uid, &effective_uid, &saved_uid), "getresuid");
    require(S3(SYS_getresgid, &real_gid, &effective_gid, &saved_gid), "getresgid");
    if (real_uid != SOLVER_ID || effective_uid != SOLVER_ID || saved_uid != SOLVER_ID)
        fail("verify_uids", -EPERM);
    if (real_gid != SOLVER_ID || effective_gid != SOLVER_ID || saved_gid != SOLVER_ID)
        fail("verify_gids", -EPERM);
    count = S2(SYS_getgroups, 0, 0);
    require(count, "getgroups_count");
    if (count > 64) fail("too_many_supplemental_groups", -EPERM);
    if (count) {
        value = S2(SYS_getgroups, count, groups);
        require(value, "getgroups");
        if (value != count) fail("group_count_changed", -EPERM);
        /* setgroups is unavailable under egress policy. Private paths are
         * root-only and trusted code/config cannot be written via GID 0. */
        for (long i = 0; i < count; i++)
            if (groups[i] != 0) fail("unexpected_supplemental_group", -EPERM);
    }
    require(P2(PR_SET_NO_NEW_PRIVS, 1), "no_new_privs");
    if (P2(PR_GET_NO_NEW_PRIVS, 0) != 1) fail("verify_no_new_privs", -EPERM);
    require(S2(SYS_capset, &header, capabilities), "clear_capabilities");
    require(S2(SYS_capget, &header, capabilities), "read_capabilities");
    for (int i = 0; i < 2; i++)
        if (capabilities[i].effective || capabilities[i].permitted || capabilities[i].inheritable)
            fail("nonzero_capabilities", -EPERM);
    for (int capability = 0; capability < 64; capability++) {
        value = P2(PR_CAPBSET_READ, capability);
        if (value == -EINVAL && capability > 0) break;
        require(value, "read_bounding_capability");
        if (value) fail("nonzero_bounding_capability", -EPERM);
        value = kernel(SYS_prctl, PR_CAP_AMBIENT, PR_CAP_AMBIENT_IS_SET, capability, 0, 0, 0);
        require(value, "read_ambient_capability");
        if (value) fail("nonzero_ambient_capability", -EPERM);
    }
    require(S2(SYS_setrlimit, RLIMIT_CORE, &core_limit), "disable_core_dumps");
    require(P2(PR_SET_DUMPABLE, 0), "disable_dumpability");
    value = S3(SYS_execve, argv[1], argv + 1, environment);
    fail("exec_model", value);
}

/* Capture the untouched Linux entry stack before aligning the call stack. */
__asm__(".global _start\n"
        ".type _start,@function\n"
        "_start:\n"
        "mov %rsp,%rdi\n"
        "and $-16,%rsp\n"
        "call demote_main\n"
        "ud2\n"
        ".size _start,.-_start\n"
        ".section .note.GNU-stack,\"\",@progbits\n"
        ".text\n");
