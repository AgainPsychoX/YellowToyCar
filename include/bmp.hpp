#pragma once
#include <cstdint>

namespace bmp {

static constexpr uint16_t expectedSignature = 0x4D42; // 'BM'

enum compression_t : uint32_t {
	BI_RGB              = 0, // most common, no compression
	BI_RLE8	            = 1, //	RLE 8bpp, only for 8bpp bitmaps
	BI_RLE4             = 2, // RLE 4bpp, only for 4bpp bitmaps
	BI_BITFIELDS        = 3, // RGB/RGBA bit field masks used
	BI_JPEG             = 4, 
	BI_PNG              = 5,
	BI_ALPHABITFIELDS   = 6,
	BI_CMYK             = 11,
	BI_CMYKRLE8         = 12,
	BI_CMYKRLE4         = 13,
};

#pragma pack(push, 1)

struct BITMAPFILEHEADER {
	uint16_t signature = expectedSignature; 
	uint32_t size; // in bytes
	uint16_t reserved1;
	uint16_t reserved2;
	uint32_t offsetToPixelArray;
};

struct BITMAPINFOHEADER {
	uint32_t headerSize = 40;	// size of this header in bytes
	int32_t width;				// width in pixels
	int32_t height;				// height in pixels
	uint16_t planes = 1;		// number of color planes
	uint16_t bitsPerPixel;		// number of bits per pixel (aka color depth)
	compression_t compression;	// compression/encoding method being used
	uint32_t imageSize;			// size of the raw bitmap data in bytes; a dummy 0 can be given for BI_RGB bitmaps
	int32_t xResolution = 0;	// horizontal resolution in pixels-per-meter
	int32_t yResolution = 0;	// vertical resolution in pixels-per-meter
	uint32_t colorsUsed;		// number of colors in the color palette, or 0 to default to 2^N
	uint32_t colorsImportant;	// number of important colors used, or 0 when every color is important; often ignored
};
static_assert(sizeof(BITMAPINFOHEADER) == 40);

/// Warning: Please prefer to use BITMAPV3INFOHEADER as some software expects
/// alpha mask when BI_BITFIELDS compression/encoding is used. Windows has some 
/// problem supporting BITMAPV2INFOHEADER: masks can be used (via BI_BITFIELDS),
/// but header size must be 40.
struct BITMAPV2INFOHEADER : BITMAPINFOHEADER {
	BITMAPV2INFOHEADER()
		: BITMAPINFOHEADER()
	{
		headerSize = 52;
	}

	uint32_t redMask;
	uint32_t greenMask;
	uint32_t blueMask;
};
static_assert(sizeof(BITMAPV2INFOHEADER) == 52);

struct BITMAPV3INFOHEADER : BITMAPV2INFOHEADER {
	BITMAPV3INFOHEADER()
		: BITMAPV2INFOHEADER()
	{
		headerSize = 56;
	}

	uint32_t alphaMask;
};
static_assert(sizeof(BITMAPV3INFOHEADER) == 56);

struct ColorTableEntry
{
	uint8_t r, g, b, _reserved;
};

#pragma pack(pop)

}
