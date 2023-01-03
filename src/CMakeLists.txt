
FILE(GLOB_RECURSE APP_SOURCES 
	${CMAKE_SOURCE_DIR}/src/*.c
	${CMAKE_SOURCE_DIR}/src/*.cpp
)

idf_component_register(
	SRCS ${APP_SOURCES}
	INCLUDE_DIRS "."
	# Note: EMBED_FILES here won't work while working with PlatformIO, see KNOWN_ISSUES.md
	EMBED_FILES "index.html.gz"
)
