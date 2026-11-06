// The dataset is compiled into the binary. cafes.py writes generated/dataset_<target>.inc (a comma
// separated byte list); build.sh passes its path as CT_DATASET_INC. The .inc holds GPS data and is
// gitignored.
#include <cstddef>

#ifndef CT_DATASET_INC
#error "CT_DATASET_INC must name the generated dataset include (see build.sh)"
#endif

alignas(8) static const unsigned char kDataset[] = {
#include CT_DATASET_INC
};

const unsigned char* ct_embedded_data() { return kDataset; }
size_t ct_embedded_size() { return sizeof(kDataset); }
