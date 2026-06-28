%module sionna_channel_swig

%define SIONNA_CHANNEL_API
%enddef

%include "gnuradio.i"
%include "std_complex.i"
%include "std_vector.i"

%{
#ifndef SWIGPY_SLICE_ARG
#define SWIGPY_SLICE_ARG(object) ((PyObject*)(object))
#endif
#include <gnuradio/sionna_channel/sparse_channel_cc.h>
%}

namespace std {
%template(gr_complex_vector) vector<complex<float> >;
%template(delay_vector) vector<unsigned short>;
}

%include <gnuradio/sionna_channel/sparse_channel_cc.h>

%template(prepared_channel_sptr) boost::shared_ptr<gr::sionna_channel::prepared_channel>;

GR_SWIG_BLOCK_MAGIC2(sionna_channel, sparse_channel_cc);
