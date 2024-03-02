#include <cstdint>
#include <cassert>
#include <cmath>
#include <iostream>
#include <fstream>
#include <vector>
#include <algorithm>
#include <random>
#include <filesystem>
#include "../include/bmp.hpp"
using namespace bmp;

namespace fs = std::filesystem;

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

std::default_random_engine randomEngine;
std::uniform_real_distribution<float> normalFloatDistribution(0, 1);

float generateRandomNormalFloat()
{
	return normalFloatDistribution(randomEngine);
}

// TODO: debug the noise: color difference between neighbour pixels larger than 1 in some cases
// #define DEBUG_NOISE

// #define SIMPLE_TEXTURE

// #define BITMAP_HEADER_V3

// For grayscale bitmaps (with 8 bpp color depth), there seem to be 2 methods:
// + using color table (only for <= 8 bpp, require color table)
// + using BI_BITFIELDS compression, having the same mask for each color component
// See https://stackoverflow.com/questions/11086649/what-is-the-bmp-format-for-gray-scale-images

// Note for debugging:
// + ImageMagick `identify -verbose output.bmp` is very useful.
// Also by the way, here is ImageMagick BMP codec:
// https://github.com/ImageMagick/ImageMagick/blob/e287a71bfb1c1d5ce467525bc08b5ed6e0d80503/coders/bmp.c

constexpr int32_t maxWidth = 1920;
constexpr int32_t maxHeight = 1080;
constexpr uint8_t supportedBitsPerPixel[] = {
	1,  // Works.
	2,  // Works.
	4,  // Works.
	8,  // Color table is still mandatory, works nicely everywhere.
	16, // Works in Windows, Paint, but doesn't work in Chrome or VS Code (looks weird)
	24, // Looks weird in Windows & Paint, doesn't work anywhere else.
	32, // Doesn't work anywhere.
};

/// Returns grayscale texture value for given normalized position.
float textureForPosition(float u, float v)
{
#ifdef SIMPLE_TEXTURE
	return u;
#else
	bool smallBox = (0.4f < v && v < 0.6f) && (0.4f < u && u < 0.6f);
	bool largeBox = (0.2f < v && v < 0.8f) && (0.2f < u && u < 0.8f);
	if (smallBox) {
		return (v - 0.4f) / 0.2f;
	}
	if (largeBox) {
		return 1 - (u + v - 0.4f) / 1.2f;
	}
	return u;
#endif
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

	// Sadly, it seems color table is required for 8 bits per pixel and less
	bool useColorTable = bitsPerPixel <= 8; 
	// bool useColorTable = false;

	bool noise = true; // TODO: make it switch

	// Prepare headers
	BITMAPFILEHEADER fileHeader;
	fileHeader.reserved1 = fileHeader.reserved2 = 0x4141;
#ifdef BITMAP_HEADER_V3
	BITMAPV3INFOHEADER dibHeader;
#else
	BITMAPV2INFOHEADER dibHeader;
#endif
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
		dibHeader.blueMask  = ~(0xFFFFFFFFLU << bitsPerPixel);
		// dibHeader.alphaMask = 0;
	}

#ifdef BITMAP_HEADER_V3
	size_t dibHeaderRealSize = dibHeader.headerSize;
	assert(dibHeaderRealSize == sizeof(BITMAPV3INFOHEADER));
#else
	// Despite header being in fact 52, it needs to be 40 for Windows to understand it
	size_t dibHeaderRealSize = dibHeader.headerSize;
	dibHeader.headerSize = sizeof(BITMAPINFOHEADER);
#endif

	// Calculate sizes, offsets, lengths
	const float bytesPerPixel = static_cast<float>(bitsPerPixel) / 8;
	const size_t rowLength = paddedToCeil(4, static_cast<size_t>(std::ceil(width * bytesPerPixel)));
	dibHeader.imageSize = rowLength * std::abs(height); 
	fileHeader.offsetToPixelArray = sizeof(fileHeader) + dibHeaderRealSize + colorTableBytesSize;
	fileHeader.size = fileHeader.offsetToPixelArray + dibHeader.imageSize;

	// Open the output file
	const char* outputPath = argc > 1 ? argv[1] : "output.bmp";
	std::ofstream output(outputPath, std::ios::binary);
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
	const float scale = std::exp2f(bitsPerPixel);
	std::vector<uint8_t> rowBuffer(rowLength, 0u);
	chunk_t* chunkPointer;
	chunk_t chunk;
	uint8_t shift;
	for (int32_t y = 0; y < height; y++) {
		const auto v = static_cast<float>(y) / height;
		rowBuffer.assign(rowLength, 0u);
		chunkPointer = reinterpret_cast<chunk_t*>(rowBuffer.data());
		chunk = 0;
		shift = 0;
#ifdef DEBUG_NOISE
		chunk_t previous = 0xFF;
#endif

		for (int32_t x = 0; x < width; x++) {
			chunk_t value;
			if (noise) {
				const auto u = static_cast<float>(x) / (width - 1);
				const auto real = textureForPosition(u, v) * (scale - 1);
				const auto prev = static_cast<chunk_t>(real);
				const auto next = prev + 1;
				value = (real - prev) < generateRandomNormalFloat() ? prev : next;
#ifdef DEBUG_NOISE
				// if (y == 0 && ((0 <= x && x <= 10) || (width - 10 <= x && x <= width))) {
				if (y == 0) {
					bool weird = std::abs((long long)previous - value) > 1 && !(previous == 0xFF && value == 0x00);
					std::printf("x=%u u=%.6f r=%.6f p=%u n=%u %%=%.3f v=%u w=%u\n", x, u, real, prev, next, (real - prev), value, weird);
				}
				previous = value;
#endif
			}
			else {
				const auto u = static_cast<float>(x) / width;
				value = static_cast<chunk_t>(textureForPosition(u, v) * scale);
			}

			chunk |= value << shift;
			shift += bitsPerPixel;

			if (shift >= chunkBits) {
				*chunkPointer++ = chunk;

				shift -= chunkBits;
				if (shift != 0) /* remaining from previous pixel */ {
					chunk = value >> (bitsPerPixel - shift);
				}
				else {
					chunk = 0;
				}
			}
		}

		if (shift != 0) /* remaining */ {
			*chunkPointer++ = chunk;
		}

#ifdef DEBUG_NOISE
		if (noise) {
			for (size_t i = 1; i < rowBuffer.size(); i++) {
				if (std::abs(rowBuffer[i - 1] - rowBuffer[i]) > 1) {
					std::fprintf(stderr, "Oops @ 0x%04X -> 0x%0X next to 0x%0X\n", 
						static_cast<int32_t>(output.tellp()), rowBuffer[i - 1], rowBuffer[i]);
				}
			}
		}
#endif

		assert(rowBuffer.size() == rowLength);
		output.write(reinterpret_cast<char*>(rowBuffer.data()), rowLength);
	}

	std::fprintf(stderr, "End of file @ 0x%04X (total length=%u)\n", 
		static_cast<int32_t>(output.tellp()), static_cast<int32_t>(output.tellp()));
	assert(output.tellp() == fileHeader.size);

	std::cout << "Done, saved at " << fs::absolute(outputPath).generic_string() << std::endl;
	return 0;
}
