//
// Copyright 2026 Gordon Chapman
// SPDX-License-Identifier: Apache-2.0
//
#include "pxr/pxr.h"

#include "resolverContext.h"

#include "pxr/base/tf/pyUtils.h"
#include "pyCompat.h"
#include "pxr/usd/ar/pyResolverContext.h"

#include <string>

PXR_NAMESPACE_USING_DIRECTIVE

using namespace assetlib_py;

using This = AssetLibResolverContext;

static size_t
_Hash(const This& ctx)
{
    return hash_value(ctx);
}

static dict
_GetPinsDict(const This& ctx)
{
    dict d;
    for (const auto& [name, ver] : ctx.GetPins()) {
        d[name] = ver;
    }
    return d;
}

static std::string
_Repr(const This& ctx)
{
    if (!ctx.GetManifestPath().empty()) {
        return TF_PY_REPR_PREFIX + "ResolverContext('" +
            ctx.GetManifestPath() + "')";
    }
    return TF_PY_REPR_PREFIX + "ResolverContext.FromPins(" +
        TfPyRepr(_GetPinsDict(ctx)) + ")";
}

static This
_FromPins(const dict& pins, const std::string& libraryRoot)
{
    This::PinMap map;
    const list items = pins.items();
    for (long i = 0, n = len(items); i < n; ++i) {
        const std::string name = extract<std::string>(items[i][0]);
        const std::string ver = extract<std::string>(items[i][1]);
        map[name] = ver;
    }
    return This(map, libraryRoot);
}

void
wrapResolverContext()
{
    class_<This>("ResolverContext", init<>())
        .def(init<const std::string&>(arg("manifestPath")))

        .def("FromPins", &_FromPins,
             (arg("pins"), arg("libraryRoot") = std::string()))
        .staticmethod("FromPins")

        .def("GetManifestPath", &This::GetManifestPath,
             return_value_policy<return_by_value>())
        .def("GetLibraryRoot", &This::GetLibraryRoot)
        .def("GetPins", &_GetPinsDict)
        .def("Reload", &This::Reload)

        .def(self == self)
        .def(self != self)
        .def("__hash__", &_Hash)
        .def("__repr__", &_Repr)
        .def("__str__", &This::GetAsString)
        ;

    ArWrapResolverContextForPython<This>();
}
