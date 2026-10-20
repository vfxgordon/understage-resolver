//
// Copyright 2026 Gordon Chapman
// SPDX-License-Identifier: Apache-2.0
//
#ifndef ASSETLIBRESOLVER_API_H
#define ASSETLIBRESOLVER_API_H

#include "pxr/base/arch/export.h"

#if defined(ASSETLIBRESOLVER_EXPORTS)
#   define ASSETLIBRESOLVER_API ARCH_EXPORT
#else
#   define ASSETLIBRESOLVER_API ARCH_IMPORT
#endif

#endif
