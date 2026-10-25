/* Calls, function pointers, switch tables, recursion. */
extern int fw_func(int a, int b, int c, int d, int e);
typedef int (*op_fn)(int, int);

static int op_add(int a, int b) { return a + b; }
static int op_sub(int a, int b) { return a - b; }
static const op_fn ops[2] = { op_add, op_sub };

int apply(int which, int a, int b) { return ops[which & 1](a, b); }

int call_fw(int x) { return fw_func(x, 1, 2, 3, 4) + 1; }

int classify(int c)
{
    switch (c) {
    case 0: return 10;
    case 1: return 20;
    case 2: return 33;
    case 3: return 47;
    case 4: return 51;
    case 5: return 68;
    case 6: return 70;
    case 7: return 99;
    default: return -1;
    }
}

unsigned fib(unsigned n) { return n < 2 ? n : fib(n - 1) + fib(n - 2); }

int many_live(int a, int b, int c, int d)
{
    int x = fw_func(a, b, c, d, a);
    int y = fw_func(x, a, b, c, d);
    return x * a + y * b + c - d;
}
