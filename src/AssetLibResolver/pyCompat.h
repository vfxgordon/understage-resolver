//
// Copyright 2026 Gordon Chapman
// SPDX-License-Identifier: Apache-2.0
//
// Boost.Python, whichever flavour this USD build uses:
//   - USD >= 24.11 ships its own copy:        pxr_boost::python
//   - Houdini <= 20.5 (USD 24.03) uses SideFX's renamed Boost: hboost::python
//   - other older USD builds use Boost:       boost::python
// The wrap*.cpp files use the namespace alias assetlib_py.
//
#ifndef ASSETLIBRESOLVER_PY_COMPAT_H
#define ASSETLIBRESOLVER_PY_COMPAT_H

#include "pxr/pxr.h"

#if PXR_VERSION >= 2411

#include "pxr/external/boost/python/class.hpp"
#include "pxr/external/boost/python/def.hpp"
#include "pxr/external/boost/python/dict.hpp"
#include "pxr/external/boost/python/extract.hpp"
#include "pxr/external/boost/python/list.hpp"
#include "pxr/external/boost/python/operators.hpp"
#include "pxr/external/boost/python/return_by_value.hpp"
#include "pxr/external/boost/python/return_value_policy.hpp"
namespace assetlib_py = PXR_NS::pxr_boost::python;

#elif __has_include(<hboost/python/class.hpp>)

#include <hboost/python/class.hpp>
#include <hboost/python/def.hpp>
#include <hboost/python/dict.hpp>
#include <hboost/python/extract.hpp>
#include <hboost/python/list.hpp>
#include <hboost/python/operators.hpp>
#include <hboost/python/return_by_value.hpp>
#include <hboost/python/return_value_policy.hpp>
namespace assetlib_py = hboost::python;

#else

#include <boost/python/class.hpp>
#include <boost/python/def.hpp>
#include <boost/python/dict.hpp>
#include <boost/python/extract.hpp>
#include <boost/python/list.hpp>
#include <boost/python/operators.hpp>
#include <boost/python/return_by_value.hpp>
#include <boost/python/return_value_policy.hpp>
namespace assetlib_py = boost::python;

#endif

#endif
