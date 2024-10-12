
idf_component_register(
	SRC_DIRS "."
	INCLUDE_DIRS "." "../libs/constexpr-to-string"
	EMBED_FILES "index.html.gz"
)

component_compile_options(-Wno-missing-field-initializers -DJSMN_HEADER)
