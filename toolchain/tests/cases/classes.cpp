// Freestanding C++: classes, templates, virtual calls; no exceptions, RTTI,
// global constructors or standard library.
template <typename T, int N>
struct Ring {
    T items[N];
    int head, count;
    bool push(T v)
    {
        if (count == N)
            return false;
        items[(head + count++) % N] = v;
        return true;
    }
    T pop() { T v = items[head]; head = (head + 1) % N; count--; return v; }
};

// A pure virtual function needs this handler (normally in libsupc++).
extern "C" void __cxa_pure_virtual() { for (;;) ; }

struct Shape {
    virtual int area() const = 0;
};
struct Rect : Shape {
    int w, h;
    Rect(int w_, int h_) : w(w_), h(h_) {}
    int area() const { return w * h; }
};

extern "C" int ring_demo(int a, int b)
{
    Ring<int, 4> r = {{0}, 0, 0};
    r.push(a);
    r.push(b);
    return r.pop() - r.pop();
}

extern "C" int area_of(const Shape *s) { return s->area(); }

extern "C" int rect_area(int w, int h)
{
    Rect r(w, h);
    return area_of(&r);
}
