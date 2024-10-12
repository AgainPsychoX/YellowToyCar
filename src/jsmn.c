
// > Since jsmn is a single-header, header-only library, for more complex use 
// > cases you might need to define additional macros. [...] 
// > Also, if you want to include `jsmn.h` from multiple C files, 
// > to avoid duplication of symbols you may define JSMN_HEADER macro.
// Source: https://github.com/zserge/jsmn?tab=readme-ov-file#usage
#undef JSMN_HEADER // globally defined EXCEPT here
#include <jsmn.h>
