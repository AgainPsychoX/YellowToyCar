
### Some known issues about the project

* C/C++ compiler used is quite old and includes decade old known GCC bug related to `struct`s aggregate initializers. See [discussion here](https://stackoverflow.com/questions/70172941/c99-designator-member-outside-of-aggregate-initializer). As solution I found out its easiest to use `strncpy` which gets inlined/optimized away.
* [The PlatformIO docs about embedding files](https://docs.platformio.org/en/latest/platforms/espressif32.html#embedding-binary-data) suggest to use prefix `_binary_src_` while accessing the start/end labels of embedded data blocks (like in  `GENERATE_HTTPD_HANDLER_FOR_EMBEDDED_FILE` macro), its not true. The docs seems outdated or invalid in some areas, at least for `esp-idf`. However I found **solution**: Use both `board_build.embed_files` in `platformio.ini` and also `EMBED_FILES` in `CMakeLists.txt`. In code, use `_binary_`, without `src_` part.
* ...
