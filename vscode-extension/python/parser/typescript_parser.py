import os
from tree_sitter_language_pack import get_parser

def extract_headers_and_bodies(file_path: str) -> list[dict]:
    """
    Parses a .ts or .tsx file and returns a list of dicts with:
      - node_type: one of
          'import_statement', 'enum_declaration', 'type_alias_declaration',
          'interface_declaration', 'class_declaration', 'method_definition',
          'function_declaration', 'variable_declaration', 'constant_declaration'
      - name: identifier for functions/classes/variables (None for imports/exports without a name)
      - header: the signature or full import/export line (up to the start of the body)
      - body: the body text (e.g., function body, class body), or None if not applicable
    """
    # Read the file as bytes
    with open(file_path, "rb") as f:
        source_bytes = f.read()

    # Choose the correct parser based on extension
    _, ext = os.path.splitext(file_path.lower())
    if ext == ".ts":
        parser = get_parser("typescript")
    else:  # ".tsx"
        parser = get_parser("tsx")

    tree = parser.parse(source_bytes)
    root = tree.root_node

    results = []
    for node in root.named_children:
        nt = node.type

        # -----------------------
        # 1. Import statements
        # -----------------------
        if nt == "import_statement":
            header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
            results.append({
                "node_type": "import_statement",
                "name": None,
                "header": header,
                "body": None
            })
            continue

        # --------------------------------
        # 2. Exported enum/type/interface
        # --------------------------------
        # An exported enum/type/interface is wrapped in an export_statement whose child 'declaration'
        # has type 'enum_declaration', 'type_alias_declaration', or 'interface_declaration'. 
        if nt == "export_statement":
            decl = node.child_by_field_name("declaration")
            # 2.1. Enum Declaration
            if decl and decl.type == "enum_declaration":
                _extract_enum(decl, source_bytes, results, exported=True)
                continue
            # 2.2. Type Alias Declaration
            if decl and decl.type == "type_alias_declaration":
                _extract_type_alias(decl, source_bytes, results, exported=True)
                continue
            # 2.3. Interface Declaration
            if decl and decl.type == "interface_declaration":
                _extract_interface(decl, source_bytes, results, exported=True)
                continue
            # 2.4. Export Default Function
            if decl and decl.type == "function_declaration":
                _extract_function(decl, source_bytes, results, exported=True)
                continue
            # 2.5. Re-export (e.g., "export { A } from 'mod';") or exported const/let/var
            named_child = node.named_children[0] if node.named_children else None
            if named_child and named_child.type in ("variable_statement", "class_declaration"):
                # Handle exported class or variable here
                if named_child.type == "class_declaration":
                    _extract_class(named_child, source_bytes, results, exported=True)
                else:
                    _process_ts_variables(named_child, source_bytes, results, exported=True)
                continue

            # If we reach here, it’s a bare "export_statement" (e.g., "export { X } from '...';")
            header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
            results.append({
                "node_type": "export_statement",
                "name": None,
                "header": header,
                "body": None
            })
            continue

        # ------------------------
        # 3. Top-level declarations
        # ------------------------

        # 3.1. Enum Declaration (non-exported)
        if nt == "enum_declaration":
            _extract_enum(node, source_bytes, results, exported=False)
            continue

        # 3.2. Type Alias Declaration (non-exported)
        if nt == "type_alias_declaration":
            _extract_type_alias(node, source_bytes, results, exported=False)
            continue

        # 3.3. Interface Declaration (non-exported)
        if nt == "interface_declaration":
            _extract_interface(node, source_bytes, results, exported=False)
            continue

        # 3.4. Function Declaration (non-exported)
        if nt == "function_declaration":
            _extract_function(node, source_bytes, results, exported=False)
            continue

        # 3.5. Class Declaration (non-exported)
        if nt == "class_declaration":
            _extract_class(node, source_bytes, results, exported=False)
            continue

        # 3.6. Variable Statement (non-exported)
        if nt == "variable_statement":
            _process_ts_variables(node, source_bytes, results, exported=False)
            continue

        # 3.7. Other top-level nodes (e.g., namespace/module_declaration)
        # You can add handlers for 'module_declaration', 'namespace_declaration' if needed.
        # For now, we skip unrecognized top-level nodes.
        continue

    return results


def _extract_enum(node, source_bytes: bytes, out_list: list, exported: bool):
    """
    Handle an 'enum_declaration' node. Splits header/body and captures the enum name.
    """
    name_node = node.child_by_field_name("name")
    name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8") if name_node else None

    # The body is the brace-enclosed list of members
    body_node = node.child_by_field_name("body")
    if body_node:
        # header: from start of 'enum' to just before '{'
        header = source_bytes[node.start_byte : body_node.start_byte].decode("utf8")
        # include '{ ... }' in body
        body = source_bytes[body_node.start_byte : node.end_byte].decode("utf8")
    else:
        header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
        body = None

    node_type = "enum_declaration"
    if exported:
        node_type = "exported_enum_declaration"
    out_list.append({
        "node_type": node_type,
        "name": name,
        "header": header,
        "body": body
    })


def _extract_type_alias(node, source_bytes: bytes, out_list: list, exported: bool):
    """
    Handle a 'type_alias_declaration' node. Captures the type name and the full line.
    """
    name_node = node.child_by_field_name("name")
    name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8") if name_node else None

    # The entire node (including body after '=')
    header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
    node_type = "type_alias_declaration"
    if exported:
        node_type = "exported_type_alias"
    out_list.append({
        "node_type": node_type,
        "name": name,
        "header": header,
        "body": None  # type aliases do not have a separate runtime body
    })


def _extract_interface(node, source_bytes: bytes, out_list: list, exported: bool):
    """
    Handle an 'interface_declaration' node. Splits header/body and captures interface name.
    """
    name_node = node.child_by_field_name("name")
    name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8") if name_node else None

    # The body is the brace-enclosed block of properties/methods
    body_node = node.child_by_field_name("body")
    if body_node:
        header = source_bytes[node.start_byte : body_node.start_byte].decode("utf8")
        body = source_bytes[body_node.start_byte : node.end_byte].decode("utf8")
    else:
        header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
        body = None

    node_type = "interface_declaration"
    if exported:
        node_type = "exported_interface"
    out_list.append({
        "node_type": node_type,
        "name": name,
        "header": header,
        "body": body
    })


def _extract_function(node, source_bytes: bytes, out_list: list, exported: bool):
    """
    Handle a 'function_declaration' node. Splits header/body and captures name.
    """
    name_node = node.child_by_field_name("name")
    name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8") if name_node else None

    body_node = node.child_by_field_name("body")
    if body_node:
        header = source_bytes[node.start_byte : body_node.start_byte].decode("utf8")
        body = source_bytes[body_node.start_byte : node.end_byte].decode("utf8")
    else:
        header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
        body = None

    node_type = "function_declaration"
    if exported:
        node_type = "exported_function_declaration"
    out_list.append({
        "node_type": node_type,
        "name": name,
        "header": header,
        "body": body
    })
    # Recurse into nested bodies to capture inner functions
    if body_node:
        for child in body_node.named_children:
            if child.type == "function_declaration":
                _extract_function(child, source_bytes, out_list, exported=False)


def _extract_class(node, source_bytes: bytes, out_list: list, exported: bool):
    """
    Handle a 'class_declaration' node. Splits header/body, captures class name,
    and extracts nested methods.
    """
    name_node = node.child_by_field_name("name")
    name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8") if name_node else None

    body_node = node.child_by_field_name("body")
    if body_node:
        header = source_bytes[node.start_byte : body_node.start_byte].decode("utf8")
        body = source_bytes[body_node.start_byte : node.end_byte].decode("utf8")
    else:
        header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
        body = None

    node_type = "class_declaration"
    if exported:
        node_type = "exported_class_declaration"
    out_list.append({
        "node_type": node_type,
        "name": name,
        "header": header,
        "body": body
    })

    # Extract methods inside the class
    if body_node:
        for member in body_node.named_children:
            if member.type == "method_definition":
                _extract_method(member, source_bytes, out_list, class_name=name, exported=False)


def _extract_method(node, source_bytes: bytes, out_list: list, class_name: str, exported: bool):
    """
    Handle a 'method_definition' node within a class. Splits header/body and captures name.
    """
    name_node = node.child_by_field_name("name")
    name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8") if name_node else None

    body_node = node.child_by_field_name("body")
    if body_node:
        header = source_bytes[node.start_byte : body_node.start_byte].decode("utf8")
        body = source_bytes[body_node.start_byte : node.end_byte].decode("utf8")
    else:
        header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
        body = None

    node_type = f"method_definition_{class_name}"
    if exported:
        node_type = f"exported_method_definition_{class_name}"
    out_list.append({
        "node_type": node_type,
        "name": name,
        "header": header,
        "body": body
    })


def _process_ts_variables(node, source_bytes: bytes, out_list: list, exported: bool):
    """
    Handle a 'variable_statement' node. Captures only significant variables:
      - Function assignments (arrow_function or function_expression)
      - Constants (uppercase with underscores)
      - Exported constants
    """
    # 'declaration_list' is the child of 'variable_statement'
    decl_list = node.child_by_field_name("declaration_list")
    if not decl_list:
        return

    for var_decl in decl_list.named_children:  # each 'variable_declarator'
        if var_decl.type != "variable_declarator":
            continue
        name_node = var_decl.child_by_field_name("name")
        value_node = var_decl.child_by_field_name("value")

        if not name_node:
            continue
        name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8")

        # 1. Function assignment (arrow or function expression)
        if value_node and value_node.type in ("arrow_function", "function_expression", "generator_function"):
            # header: from 'const foo = ' up to start of function body
            # find the start of the function’s body
            func_body = None
            if value_node.child_by_field_name("body"):
                fb = value_node.child_by_field_name("body")
                header = source_bytes[node.start_byte : fb.start_byte].decode("utf8")
                func_body = source_bytes[fb.start_byte : value_node.end_byte].decode("utf8")
            else:
                header = source_bytes[node.start_byte : node.end_byte].decode("utf8")

            node_type = "function_assignment"
            if exported:
                node_type = "exported_function_assignment"
            out_list.append({
                "node_type": node_type,
                "name": name,
                "header": header,
                "body": func_body
            })
            continue

        # 2. Constants by naming convention or explicit 'const'
        decl_text = source_bytes[node.start_byte : node.end_byte].decode("utf8")
        if name.isupper() and "_" in name:
            node_type = "constant_declaration"
            if exported:
                node_type = "exported_constant_declaration"
            out_list.append({
                "node_type": node_type,
                "name": name,
                "header": decl_text,
                "body": None
            })
            continue

        # 3. General variable (skip 'let'/'var' for now) 
        # If desired, you could capture 'let' or 'var' similarly:
        # if value_node and value_node.type not in ("arrow_function", ...):
        #     ...
        # But for a rough summary, skip non-constants.
        continue
