from tree_sitter_language_pack import get_parser

def extract_headers_and_bodies(file_path: str) -> list[dict]:
    """
    Parses the given C++ file and returns a list of dicts, each containing:
      - node_type: one of
          'preproc_include', 'using_directive', 'using_declaration',
          'preproc_def', 'preproc_func_def',
          'namespace_definition', 'class_specifier', 'struct_specifier',
          'enum_specifier', 'template_declaration',
          'function_prototype', 'function_definition'
      - name: identifier for types, functions, methods, or None if not applicable
      - header: source text for the signature or full directive
      - body: source text for the body block (or None if no body)
    Applies filters:
      1. Skips 'comment' nodes.
      2. Captures top-level includes, using directives, macros.
      3. Distinguishes function prototypes from generic declarations.
      4. Recurse into namespaces and types to capture public members only.
    """
    with open(file_path, "rb") as f:
        source_bytes = f.read()

    parser = get_parser("cpp")
    tree = parser.parse(source_bytes)
    root = tree.root_node  # "translation_unit"

    results = []
    for node in root.named_children:
        nt = node.type

        # 1. Skip comments
        if nt == "comment":
            continue

        # 2.1 Include directives
        if nt == "preproc_include":
            header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
            results.append({
                "node_type": nt,
                "name": None,
                "header": header,
                "body": None
            })
            continue

        # 2.2 Using directives / using_declarations
        if nt in ("using_directive", "using_declaration"):
            header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
            results.append({
                "node_type": nt,
                "name": None,
                "header": header,
                "body": None
            })
            continue

        # 2.3 Macro definitions (#define)
        if nt in ("preproc_def", "preproc_func_def"):
            header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
            results.append({
                "node_type": nt,
                "name": None,
                "header": header,
                "body": None
            })
            continue

        # 2.4 Function prototype at top level (declaration + parameter_list)
        if nt == "declaration":
            declarator = node.child_by_field_name("declarator")
            if declarator:
                param_list = declarator.child_by_field_name("parameter_list")
                if param_list:
                    fn_name = None
                    for c in reversed(declarator.named_children):
                        if c.type == "identifier" and c.end_byte <= param_list.start_byte:
                            fn_name = source_bytes[c.start_byte : c.end_byte].decode("utf8")
                            break

                    header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
                    results.append({
                        "node_type": "function_prototype",
                        "name": fn_name,
                        "header": header,
                        "body": None
                    })
                    continue
            # Not a prototype -> skip
            continue

        # 3. Namespace definitions
        if nt == "namespace_definition":
            name_node = node.child_by_field_name("name")
            name = (
                source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8")
                if name_node else None
            )
            body_node = node.child_by_field_name("body")
            if body_node:
                header = source_bytes[node.start_byte : body_node.start_byte].decode("utf8")
                body   = source_bytes[body_node.start_byte : node.end_byte].decode("utf8")
            else:
                header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
                body   = None

            results.append({
                "node_type": nt,
                "name": name,
                "header": header,
                "body": body
            })

            # Recurse into namespace body
            if body_node:
                for member in body_node.named_children:
                    _process_cpp_top_level(member, source_bytes, results)
            continue

        # 4. Top-level types and templates
        if nt in ("class_specifier", "struct_specifier", "enum_specifier", "template_declaration"):
            _process_cpp_top_level(node, source_bytes, results)
            continue

        # 5. Free-function or constructor definition at top level
        if nt == "function_definition":
            declarator_node = node.child_by_field_name("declarator")
            name = None
            if declarator_node:
                # Try field_identifier first
                fi = next((c for c in declarator_node.named_children if c.type == "field_identifier"), None)
                if fi:
                    name = source_bytes[fi.start_byte : fi.end_byte].decode("utf8")
                else:
                    param_list = declarator_node.child_by_field_name("parameter_list")
                    if param_list:
                        for c in reversed(declarator_node.named_children):
                            if c.type == "identifier" and c.end_byte <= param_list.start_byte:
                                name = source_bytes[c.start_byte : c.end_byte].decode("utf8")
                                break

            body_node = node.child_by_field_name("body")
            if body_node:
                header = source_bytes[node.start_byte : body_node.start_byte].decode("utf8")
                body   = source_bytes[body_node.start_byte : node.end_byte].decode("utf8")
            else:
                header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
                body   = None

            results.append({
                "node_type": "function_definition",
                "name": name,
                "header": header,
                "body": body
            })
            continue

        # 6. Everything else -> skip
        continue

    return results


def _process_cpp_top_level(type_node, source_bytes: bytes, out_list: list):
    """
    Handles a top-level or nested type (class, struct, enum, or template),
    then recurses into its members to capture public constructors, methods,
    prototypes, using_directives, and macros.
    """
    if type_node.type == "comment":
        return

    nt = type_node.type
    name_node = type_node.child_by_field_name("name")
    name = (
        source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8")
        if name_node else None
    )

    body_node = type_node.child_by_field_name("body")
    if body_node:
        header = source_bytes[type_node.start_byte : body_node.start_byte].decode("utf8")
        body   = source_bytes[body_node.start_byte : type_node.end_byte].decode("utf8")
    else:
        header = source_bytes[type_node.start_byte : type_node.end_byte].decode("utf8")
        body   = None

    out_list.append({
        "node_type": nt,
        "name": name,
        "header": header,
        "body": body
    })

    if not body_node:
        return

    # Determine default access: 'private' for class, 'public' for struct
    default_access = "private" if nt == "class_specifier" else "public"
    current_access = default_access

    for member in body_node.named_children:
        mtype = member.type

        if mtype == "comment":
            continue

        # Update access_specifier
        if mtype == "access_specifier":
            spec = source_bytes[member.start_byte : member.end_byte].decode("utf8").strip()
            if spec.startswith("public"):
                current_access = "public"
            elif spec.startswith("private"):
                current_access = "private"
            elif spec.startswith("protected"):
                current_access = "protected"
            continue

        if current_access != "public":
            continue

        # 1. Nested using directives / using declarations
        if mtype in ("using_directive", "using_declaration"):
            header = source_bytes[member.start_byte : member.end_byte].decode("utf8")
            out_list.append({
                "node_type": mtype,
                "name": None,
                "header": header,
                "body": None
            })
            continue

        # 2. Nested macro definitions (#define)
        if mtype in ("preproc_def", "preproc_func_def"):
            header = source_bytes[member.start_byte : member.end_byte].decode("utf8")
            out_list.append({
                "node_type": mtype,
                "name": None,
                "header": header,
                "body": None
            })
            continue

        # 3. Function prototype inside class/namespace
        if mtype == "declaration":
            decl = member.child_by_field_name("declarator")
            if decl:
                param_list = decl.child_by_field_name("parameter_list")
                if param_list:
                    proto_name = None
                    for c in reversed(decl.named_children):
                        if c.type == "identifier" and c.end_byte <= param_list.start_byte:
                            proto_name = source_bytes[c.start_byte : c.end_byte].decode("utf8")
                            break

                    header = source_bytes[member.start_byte : member.end_byte].decode("utf8")
                    out_list.append({
                        "node_type": "function_prototype",
                        "name": proto_name,
                        "header": header,
                        "body": None
                    })
                    continue
            continue

        # 4. Nested type declarations (signature only)
        if mtype in ("class_specifier", "struct_specifier", "enum_specifier"):
            nested_name_node = member.child_by_field_name("name")
            nested_name = (
                source_bytes[nested_name_node.start_byte : nested_name_node.end_byte].decode("utf8")
                if nested_name_node else None
            )
            nested_body = member.child_by_field_name("body")
            if nested_body:
                nested_header = source_bytes[member.start_byte : nested_body.start_byte].decode("utf8")
            else:
                nested_header = source_bytes[member.start_byte : member.end_byte].decode("utf8")
            out_list.append({
                "node_type": mtype,
                "name": nested_name,
                "header": nested_header,
                "body": None
            })
            continue

        # 5. Member function definitions (method or constructor)
        if mtype == "function_definition":
            declarator_node = member.child_by_field_name("declarator")
            func_name = None
            if declarator_node:
                fi = next((c for c in declarator_node.named_children if c.type == "field_identifier"), None)
                if fi:
                    func_name = source_bytes[fi.start_byte : fi.end_byte].decode("utf8")
                else:
                    param_list = declarator_node.child_by_field_name("parameter_list")
                    if param_list:
                        for c in reversed(declarator_node.named_children):
                            if c.type == "identifier" and c.end_byte <= param_list.start_byte:
                                func_name = source_bytes[c.start_byte : c.end_byte].decode("utf8")
                                break

            body_node2 = member.child_by_field_name("body")
            if body_node2:
                func_header = source_bytes[member.start_byte : body_node2.start_byte].decode("utf8")
                func_body   = source_bytes[body_node2.start_byte : member.end_byte].decode("utf8")
            else:
                func_header = source_bytes[member.start_byte : member.end_byte].decode("utf8")
                func_body   = None

            if func_name == name:
                node_label = "constructor_definition"
            elif func_name == f"~{name}":
                node_label = "destructor_definition"
            else:
                node_label = "method_declaration"

            out_list.append({
                "node_type": node_label,
                "name": func_name,
                "header": func_header,
                "body": func_body
            })
            continue

        # 6. Field declarations (public only)
        if mtype == "field_declaration":
            field_header = source_bytes[member.start_byte : member.end_byte].decode("utf8")
            out_list.append({
                "node_type": mtype,
                "name": None,
                "header": field_header,
                "body": None
            })
            continue

        # 7. Everything else -> skip
        continue
