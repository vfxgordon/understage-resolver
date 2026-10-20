//
// Copyright 2026 Gordon Chapman
// SPDX-License-Identifier: Apache-2.0
//
// Test probe for FS_AssetLibHelper: loads the FS helpers the way Houdini does
// (FSloadCustomHelpers, from dso/fs on $HOUDINI_PATH) and exercises a path
// through Houdini's file layer. Prints one JSON object; driven by
// tests/test_houdini_fs.py. A standalone HDK program, so no Houdini license
// is needed.
//
//   fs_helper_probe read  <path>   -> {"ok", "content", "extension", "exists",
//                                      "writable", "size"}
//   fs_helper_probe write <path>   -> {"ok"}  (ok: the write succeeded)
//

#include <FS/FS_Info.h>
#include <FS/FS_Reader.h>
#include <FS/FS_Utils.h>
#include <FS/FS_Writer.h>
#include <UT/UT_IStream.h>
#include <UT/UT_JSONWriter.h>
#include <UT/UT_String.h>
#include <UT/UT_WorkBuffer.h>

#include <iostream>
#include <string>
#include <unistd.h>

int
main(int argc, char *argv[])
{
    if (argc != 3) {
        std::cerr << "usage: fs_helper_probe read|write <path>\n";
        return 2;
    }
    const std::string mode = argv[1];
    const char *path = argv[2];

    FSloadCustomHelpers();

    UT_AutoJSONWriter json(std::cout, /*binary=*/false);
    UT_JSONWriter &w = *json;
    w.jsonBeginMap();

    if (mode == "read") {
        std::string content;
        bool ok = false;
        {
            FS_Reader reader(path);
            if (UT_IStream *in = reader.getStream()) {
                UT_WorkBuffer buffer;
                ok = in->getAll(buffer);
                content = buffer.toStdString();
            }
        }
        FS_Info info(path);
        w.jsonKeyToken("ok");        w.jsonBool(ok);
        w.jsonKeyToken("content");   w.jsonString(content.c_str());
        w.jsonKeyToken("extension"); w.jsonString(info.getExtension().c_str());
        w.jsonKeyToken("exists");    w.jsonBool(info.exists());
        w.jsonKeyToken("writable");  w.jsonBool(info.hasAccess(W_OK));
        w.jsonKeyToken("size");      w.jsonInt(info.getFileDataSize());
    } else if (mode == "write") {
        bool ok = false;
        {
            FS_Writer writer(path);
            if (std::ostream *out = writer.getStream()) {
                *out << "written by fs_helper_probe\n";
                ok = bool(*out);
            }
        }
        w.jsonKeyToken("ok"); w.jsonBool(ok);
    } else {
        std::cerr << "unknown mode " << mode << "\n";
        return 2;
    }
    w.jsonEndMap();
    std::cout << std::endl;
    return 0;
}
