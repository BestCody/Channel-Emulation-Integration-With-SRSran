#pragma once

#include <gnuradio/attributes.h>

#ifdef gnuradio_sionna_channel_EXPORTS
#define SIONNA_CHANNEL_API __GR_ATTR_EXPORT
#else
#define SIONNA_CHANNEL_API __GR_ATTR_IMPORT
#endif
