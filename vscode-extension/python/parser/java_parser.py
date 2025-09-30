from tree_sitter_language_pack import get_parser

def extract_headers_and_bodies(file_path: str) -> list[dict]:
    """
    Parses the given Java file and returns a list of dicts, each containing:
      - node_type: one of
          'package_declaration', 'import_declaration', 'module_declaration',
          'class_declaration', 'interface_declaration', 'enum_declaration',
          'annotation_type_declaration', 'record_declaration',
          'constructor_declaration', 'method_declaration', 'field_declaration',
          'static_initializer'
      - name: identifier for types, constructors, methods (None for imports/fields)
      - header: the signature or full import/package/module line(s)
      - body: the body of type or method, or None (for imports, package, fields, abstract/interface methods)
    """
    with open(file_path, "rb") as f:
        source_bytes = f.read()

    parser = get_parser("java")
    tree = parser.parse(source_bytes)
    root = tree.root_node  # type == "translation_unit" :contentReference[oaicite:4]{index=4}

    results = []
    for node in root.named_children:
        nt = node.type

        # 1. Package declaration
        if nt == "package_declaration":
            header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
            results.append({
                "node_type": nt,
                "name": None,
                "header": header,
                "body": None
            })
            continue

        # 2. Import statements
        if nt == "import_declaration":
            header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
            results.append({
                "node_type": nt,
                "name": None,
                "header": header,
                "body": None
            })
            continue

        # 3. Module declaration (Java 9+)
        if nt == "module_declaration":
            body_node = node.child_by_field_name("body")  # the '{ … }' :contentReference[oaicite:5]{index=5}
            if body_node:
                header = source_bytes[node.start_byte : body_node.start_byte].decode("utf8")
                body   = source_bytes[body_node.start_byte : node.end_byte].decode("utf8")
            else:
                header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
                body   = None

            name_node = node.child_by_field_name("name")
            name = (
                source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8")
                if name_node else None
            )
            results.append({
                "node_type": nt,
                "name": name,
                "header": header,
                "body": body
            })
            continue

        # 4. Top‐level Type Declarations
        if nt in (
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "annotation_type_declaration",
            "record_declaration"
        ):
            _process_java_type(node, source_bytes, results)
            continue

        # 5. Everything else: skip
        continue

    return results


def _process_java_type(type_node, source_bytes: bytes, out_list: list):
    """
    Handles a top‐level type (class, interface, enum, annotation, record), then
    recurses into its body to capture constructors, methods, fields, and nested types.
    """
    nt = type_node.type  # e.g., "class_declaration"
    name_node = type_node.child_by_field_name("name")
    name = (
        source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8")
        if name_node else None
    )

    body_node = type_node.child_by_field_name("body")  # e.g., class_body :contentReference[oaicite:6]{index=6}
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

    if body_node:
        for member in body_node.named_children:
            mtype = member.type

            # a. Static initializer (static { … })
            if mtype == "static_initializer":
                init_body_node = member.child_by_field_name("body")
                if init_body_node:
                    header = source_bytes[member.start_byte : init_body_node.start_byte].decode("utf8")
                    body   = source_bytes[init_body_node.start_byte : member.end_byte].decode("utf8")
                else:
                    header = source_bytes[member.start_byte : member.end_byte].decode("utf8")
                    body   = None
                out_list.append({
                    "node_type": mtype,
                    "name": None,
                    "header": header,
                    "body": body
                })
                continue

            # b. Constructor Declaration
            if mtype == "constructor_declaration":
                ctor_name_node = member.child_by_field_name("name")
                ctor_name = (
                    source_bytes[ctor_name_node.start_byte : ctor_name_node.end_byte].decode("utf8")
                    if ctor_name_node else None
                )
                ctor_body_node = member.child_by_field_name("body")
                if ctor_body_node:
                    ctor_header = source_bytes[member.start_byte : ctor_body_node.start_byte].decode("utf8")
                    ctor_body   = source_bytes[ctor_body_node.start_byte : member.end_byte].decode("utf8")
                else:
                    ctor_header = source_bytes[member.start_byte : member.end_byte].decode("utf8")
                    ctor_body   = None
                out_list.append({
                    "node_type": mtype,
                    "name": ctor_name,
                    "header": ctor_header,
                    "body": ctor_body
                })
                continue

            # c. Method Declaration
            if mtype == "method_declaration":
                m_name_node = member.child_by_field_name("name")
                method_name = (
                    source_bytes[m_name_node.start_byte : m_name_node.end_byte].decode("utf8")
                    if m_name_node else None
                )
                m_body_node = member.child_by_field_name("body")
                if m_body_node:
                    method_header = source_bytes[member.start_byte : m_body_node.start_byte].decode("utf8")
                    method_body   = source_bytes[m_body_node.start_byte : member.end_byte].decode("utf8")
                else:
                    method_header = source_bytes[member.start_byte : member.end_byte].decode("utf8")
                    method_body   = None
                out_list.append({
                    "node_type": mtype,
                    "name": method_name,
                    "header": method_header,
                    "body": method_body
                })
                continue

            # d. Field Declaration
            if mtype == "field_declaration":
                field_header = source_bytes[member.start_byte : member.end_byte].decode("utf8")
                out_list.append({
                    "node_type": mtype,
                    "name": None,
                    "header": field_header,
                    "body": None
                })
                continue

            # e. Nested Type Declaration
            if mtype in (
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
                "annotation_type_declaration",
                "record_declaration"
            ):
                _process_java_type(member, source_bytes, out_list)
                continue

            # f. Everything else: skip
            continue
