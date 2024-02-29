#include <cstdint>
#include <cassert>
#include <cmath>
#include <iostream>
#include <fstream>
#include <vector>
#include <algorithm>
#include "../include/bmp.hpp"
using namespace bmp;

template<class C, typename T>
bool contains(C&& c, T e) 
{
	return std::find(std::begin(c), std::end(c), e) != std::end(c);
}

/// Calculates required padding to ceil up to next multiply of given number.
template <typename T>
inline std::size_t paddingToCeil(std::size_t toNumber, T value) 
{
	return ((value % toNumber > 0) ? (4 - value % toNumber) : 0);
}

/// Padded value to be ceil up to next multiply of given number.
template <typename T>
inline T paddedToCeil(std::size_t toNumber, T value) 
{
	return value + paddingToCeil(toNumber, value);
}

constexpr int32_t maxWidth = 1920;
constexpr int32_t maxHeight = 1080;
constexpr uint8_t supportedBitsPerPixel[] = { 1, 2, 4, 8, 16, 24, 32 };

// For grayscale bitmaps (with 8 bpp color depth), there seem to be 2 methods:
// + using color table (only for <= 8 bpp, require color table)
// + using BI_BITFIELDS compression, having the same mask for each color component
// See https://stackoverflow.com/questions/11086649/what-is-the-bmp-format-for-gray-scale-images

// Note for debugging:
// + ImageMagick `identify -verbose output.bmp` is very useful.

/// Returns grayscale texture value for given normalized position.
float textureForPosition(float u, float v)
{
	return u;
}

int main(int argc, char* argv[])
{
	int32_t width  = argc > 2 ? std::stoi(argv[2]) : 256;
	int32_t height = argc > 3 ? std::stoi(argv[3]) : 256;
	if (width < 0 || height == 0) {
		std::cerr << "Invalid width or height." << std::endl;
		return 1;
	}
	if (width > maxWidth || height > maxHeight) {
		std::cerr << "Max size is " << maxWidth << 'x' << maxHeight << '.' << std::endl;
		return 1;
	}
	if (height < 0) {
		std::cerr << "Top-to-bottom rows order not supported." << std::endl;
		return 1;
	}

	const uint8_t bitsPerPixel = argc > 4 ? std::stoi(argv[4]) : 8;
	if (!contains(supportedBitsPerPixel, bitsPerPixel)) {
		std::cerr << "Invalid color depth." << std::endl;
		return 1;
	}

	// TODO: check if color tables are indeed mandatory when 8 bits per pixel
	bool useColorTable = true; // FIXME: ...
	//bool useColorTable = bitsPerPixel < 8;

	// Prepare headers
	BITMAPFILEHEADER fileHeader;
	fileHeader.reserved1 = fileHeader.reserved2 = 0x4141;
	BITMAPV2INFOHEADER dibHeader;
	dibHeader.width = width;
	dibHeader.height = height;
	dibHeader.bitsPerPixel = bitsPerPixel;
	dibHeader.colorsUsed = useColorTable ? (1 << bitsPerPixel) : 0;
	std::vector<ColorTableEntry> colorTable(dibHeader.colorsUsed);
	size_t colorTableBytesSize = colorTable.size() * sizeof(ColorTableEntry);

	if (useColorTable) {
		// If color table used, no bit masks 'compression' is used, 
		// so no need for the related fields.
		dibHeader.headerSize = sizeof(BITMAPINFOHEADER); 

		dibHeader.compression = BI_RGB;

		// Prepare color table
		uint8_t i = 0;
		uint8_t step = 1 << (8 - bitsPerPixel);
		for (auto&& entry : colorTable) {
			entry = { i, i, i, 0 };
			i += step;
		}
	}
	else {
		dibHeader.compression = BI_BITFIELDS; // signal RGB masks should be used
		dibHeader.redMask   = \
		dibHeader.greenMask = \
		dibHeader.blueMask  = ~(0xFFFFFFFF << bitsPerPixel);
	}

	// Despite header being in fact 52, it needs to be 40 for Windows to understand it
	size_t dibHeaderRealSize = dibHeader.headerSize;
	dibHeader.headerSize = sizeof(BITMAPINFOHEADER);

	// Calculate sizes, offsets, lengths
	assert(bitsPerPixel % 8 == 0);
	const uint8_t bytesPerPixel = bitsPerPixel / 8;
	const size_t rowLength = paddedToCeil(4, width * bytesPerPixel);
	dibHeader.imageSize = rowLength * std::abs(height); 
	fileHeader.offsetToPixelArray = sizeof(fileHeader) + dibHeaderRealSize + colorTableBytesSize;
	fileHeader.size = fileHeader.offsetToPixelArray + dibHeader.imageSize;

	// Open the output file
	std::ofstream output(argc > 1 ? argv[1] : "output.bmp", std::ios::binary);
	if (!output.is_open()) {
		std::cerr << "Error opening files." << std::endl;
		return 1;
	}

	// Write headers & color table
	output.write(reinterpret_cast<char*>(&fileHeader), sizeof(fileHeader));
	std::fprintf(stderr, "DIB header  @ 0x%04X\n", static_cast<int32_t>(output.tellp()));
	output.write(reinterpret_cast<char*>(&dibHeader), dibHeaderRealSize);
	if (useColorTable) {
		std::fprintf(stderr, "Color table @ 0x%04X\n", static_cast<int32_t>(output.tellp()));
		output.write(reinterpret_cast<char*>(colorTable.data()), colorTableBytesSize);
	}

	std::fprintf(stderr, "Pixels data @ 0x%04X\n", static_cast<int32_t>(output.tellp()));
	assert(output.tellp() == fileHeader.offsetToPixelArray);

	// Write pixels data
	using chunk_t = uint32_t; // at least 32 bits required to ensure alignment
	constexpr auto chunkBits = sizeof(chunk_t) * 8;
	const chunk_t maxValue = ~(0xFFFFFFFF << bitsPerPixel);
	std::vector<uint8_t> rowBuffer(rowLength, 0u);
	chunk_t* chunkPointer;
	chunk_t chunk;
	int8_t shift;
	const int8_t firstPixelShift = chunkBits - bitsPerPixel;
	for (int32_t y = 0; y < height; y++) {
		const float v = static_cast<float>(y) / height;
		rowBuffer.assign(rowLength, 0u);
		chunkPointer = reinterpret_cast<chunk_t*>(rowBuffer.data());
		chunk = 0;
		shift = firstPixelShift;

		/*	Example for 3 bits per pixel, to white, with 32 bits wide chunk:
			| pos | shift | buffer bytes
			|   0 |    29 | 00000000 00000000 00000000 00000000, ...
			|   0 |    26 | 11100000 00000000 00000000 00000000, ...
			|   0 |    23 | 11111100 00000000 00000000 00000000, ...
			|   0 |    20 | 11111111 10000000 00000000 00000000, ...
			| ... | ...   | ... |
			|   0 |     1 | 11111111 11111111 11111111 11110000, ...
			|   0 | -2/30 | 11111111 11111111 11111111 11111110, ...
			|   4 |    27 | 11111111 11111111 11111111 11111111, 11000000 ...
		*/

		for (int32_t x = 0; x < width; x++) {
			const float u = static_cast<float>(x) / width;
			chunk_t value = std::lround(textureForPosition(u, v) * maxValue);

			chunk |= value << shift;
			shift -= bitsPerPixel;

			if (shift < 0) {
				chunk |= value >> -shift;

				*chunkPointer++ = chunk;

				shift += chunkBits;
				if (shift != firstPixelShift) /* remaining from previous pixel */ {
					chunk = value << shift;
				}
				else {
					chunk = 0;
				}
			}
		}

		if (shift != firstPixelShift) /* remaining */ {
			*chunkPointer++ = chunk;
		}

		assert(rowBuffer.size() == rowLength);
		output.write(reinterpret_cast<char*>(rowBuffer.data()), rowLength);
	}

	std::fprintf(stderr, "End of file @ 0x%04X (length=%u)\n", 
		static_cast<int32_t>(output.tellp()), static_cast<int32_t>(output.tellp()));
	assert(output.tellp() == fileHeader.size);

	// FIXME: crashes (most of the time) on output.close()

	std::cout << "Done." << std::endl;
	return 0;
}
