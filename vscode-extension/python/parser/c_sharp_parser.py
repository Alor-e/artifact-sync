from tree_sitter_language_pack import get_parser

def extract_headers_and_bodies(file_path: str) -> list[dict]:
    """
    Parses a C++ file and returns a list of dicts with:
      - node_type: e.g. 'preproc_include', 'namespace_definition', 
                   'class_specifier', 'function_definition', etc.
      - name: identifier for types, functions, namespaces, or None if not applicable
      - header: the source text for the signature/declaration
      - body: the source text for the body block (or None for declarations)
    
    Focuses on structural elements that provide file overview:
      - Preprocessor includes (#include)
      - Namespace definitions
      - Class/struct declarations and definitions
      - Function declarations and definitions (public/non-static)
      - Template declarations
      - Enum definitions
      - Type aliases (typedef, using)
    
    Filters out:
      - Comments
      - Private class members (when possible to detect)
      - Local variable declarations
      - Most preprocessor directives except includes
    """
    with open(file_path, "rb") as f:
        source_bytes = f.read()

    parser = get_parser("cpp")
    tree = parser.parse(source_bytes)
    root = tree.root_node  # usually a 'translation_unit'

    results = []
    for node in root.named_children:
        _process_cpp_node(node, source_bytes, results, is_top_level=True)

    return results


def _process_cpp_node(node, source_bytes: bytes, out_list: list, is_top_level: bool = False):
    """
    Processes a C++ AST node and extracts relevant structural information.
    """
    # Skip comments
    if node.type == "comment":
        return

    nt = node.type

    # 1. Preprocessor includes
    if nt == "preproc_include":
        header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
        out_list.append({
            "node_type": nt,
            "name": None,
            "header": header,
            "body": None
        })
        return

    # 2. Namespace definition
    if nt == "namespace_definition":
        name_node = node.child_by_field_name("name")
        name = (
            source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8")
            if name_node else "anonymous"
        )
        
        body_node = node.child_by_field_name("body")
        if body_node:
            header = source_bytes[node.start_byte : body_node.start_byte].decode("utf8")
            body = source_bytes[body_node.start_byte : node.end_byte].decode("utf8")
            
            # Process namespace members
            for member in body_node.named_children:
                _process_cpp_node(member, source_bytes, out_list, is_top_level=False)
        else:
            header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
            body = None

        out_list.append({
            "node_type": nt,
            "name": name,
            "header": header,
            "body": body
        })
        return

    # 3. Class/struct specifier (definition)
    if nt in ("class_specifier", "struct_specifier"):
        name_node = node.child_by_field_name("name")
        name = (
            source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8")
            if name_node else None
        )

        body_node = node.child_by_field_name("body")
        if body_node:
            header = source_bytes[node.start_byte : body_node.start_byte].decode("utf8")
            body = source_bytes[body_node.start_byte : node.end_byte].decode("utf8")
            
            # Process class members (methods, nested types, etc.)
            current_access = "private" if nt == "class_specifier" else "public"
            for member in body_node.named_children:
                if member.type == "access_specifier":
                    access_text = source_bytes[member.start_byte : member.end_byte].decode("utf8")
                    if "public" in access_text:
                        current_access = "public"
                    elif "private" in access_text:
                        current_access = "private"
                    elif "protected" in access_text:
                        current_access = "protected"
                    continue
                
                # Only process public members for classes, all members for structs
                if nt == "class_specifier" and current_access == "private":
                    continue
                    
                _process_cpp_class_member(member, source_bytes, out_list)
        else:
            header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
            body = None

        out_list.append({
            "node_type": nt,
            "name": name,
            "header": header,
            "body": body
        })
        return

    # 4. Function definition
    if nt == "function_definition":
        declarator_node = node.child_by_field_name("declarator")
        name = None
        if declarator_node:
            # Extract function name from declarator
            name = _extract_function_name(declarator_node, source_bytes)

        body_node = node.child_by_field_name("body")
        if body_node:
            header = source_bytes[node.start_byte : body_node.start_byte].decode("utf8")
            body = source_bytes[body_node.start_byte : node.end_byte].decode("utf8")
        else:
            header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
            body = None

        out_list.append({
            "node_type": nt,
            "name": name,
            "header": header,
            "body": body
        })
        return

    # 5. Function declaration (prototype)
    if nt == "declaration" and _is_function_declaration(node, source_bytes):
        declarator_node = _find_function_declarator(node)
        name = None
        if declarator_node:
            name = _extract_function_name(declarator_node, source_bytes)

        header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
        out_list.append({
            "node_type": "function_declaration",
            "name": name,
            "header": header,
            "body": None
        })
        return

    # 6. Template declaration
    if nt == "template_declaration":
        # Get the template parameters
        template_params = node.child_by_field_name("parameters")
        if template_params:
            header_end = template_params.end_byte
        else:
            header_end = node.start_byte + len("template")
            
        # Check what's being templated
        for child in node.named_children:
            if child.type in ("class_specifier", "struct_specifier", "function_definition"):
                _process_cpp_node(child, source_bytes, out_list, is_top_level)
                break
        
        # Also add the template declaration itself
        header = source_bytes[node.start_byte : header_end].decode("utf8")
        out_list.append({
            "node_type": nt,
            "name": None,
            "header": header,
            "body": None
        })
        return

    # 7. Enum specifier
    if nt == "enum_specifier":
        name_node = node.child_by_field_name("name")
        name = (
            source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8")
            if name_node else None
        )

        body_node = node.child_by_field_name("body")
        if body_node:
            header = source_bytes[node.start_byte : body_node.start_byte].decode("utf8")
            body = source_bytes[body_node.start_byte : node.end_byte].decode("utf8")
        else:
            header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
            body = None

        out_list.append({
            "node_type": nt,
            "name": name,
            "header": header,
            "body": body
        })
        return

    # 8. Type alias (typedef, using)
    if nt in ("type_definition", "alias_declaration"):
        header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
        # Try to extract the alias name
        name = _extract_type_alias_name(node, source_bytes)
        
        out_list.append({
            "node_type": nt,
            "name": name,
            "header": header,
            "body": None
        })
        return

    # 9. Using declaration/directive
    if nt in ("using_declaration", "using_directive"):
        header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
        out_list.append({
            "node_type": nt,
            "name": None,
            "header": header,
            "body": None
        })
        return


def _process_cpp_class_member(member, source_bytes: bytes, out_list: list):
    """Process members inside a class/struct body."""
    if member.type == "comment":
        return

    mtype = member.type

    # Constructor/destructor
    if mtype in ("function_definition", "declaration"):
        if mtype == "function_definition":
            declarator_node = member.child_by_field_name("declarator")
            name = None
            if declarator_node:
                name = _extract_function_name(declarator_node, source_bytes)

            body_node = member.child_by_field_name("body")
            if body_node:
                header = source_bytes[member.start_byte : body_node.start_byte].decode("utf8")
                body = source_bytes[body_node.start_byte : member.end_byte].decode("utf8")
            else:
                header = source_bytes[member.start_byte : member.end_byte].decode("utf8")
                body = None

            out_list.append({
                "node_type": "method_definition",
                "name": name,
                "header": header,
                "body": body
            })
        elif _is_function_declaration(member, source_bytes):
            declarator_node = _find_function_declarator(member)
            name = None
            if declarator_node:
                name = _extract_function_name(declarator_node, source_bytes)

            header = source_bytes[member.start_byte : member.end_byte].decode("utf8")
            out_list.append({
                "node_type": "method_declaration",
                "name": name,
                "header": header,
                "body": None
            })

    # Nested types
    elif mtype in ("class_specifier", "struct_specifier", "enum_specifier"):
        _process_cpp_node(member, source_bytes, out_list, is_top_level=False)


def _extract_function_name(declarator_node, source_bytes: bytes) -> str:
    """Extract function name from a function declarator."""
    if declarator_node.type == "function_declarator":
        declarator = declarator_node.child_by_field_name("declarator")
        if declarator:
            return source_bytes[declarator.start_byte : declarator.end_byte].decode("utf8")
    elif declarator_node.type == "identifier":
        return source_bytes[declarator_node.start_byte : declarator_node.end_byte].decode("utf8")
    
    # Fallback: try to find an identifier child
    for child in declarator_node.named_children:
        if child.type == "identifier":
            return source_bytes[child.start_byte : child.end_byte].decode("utf8")
    
    return None


def _is_function_declaration(node, source_bytes: bytes) -> bool:
    """Check if a declaration node is a function declaration."""
    # Look for function_declarator in the declaration
    return _find_function_declarator(node) is not None


def _find_function_declarator(node):
    """Find a function_declarator node within a declaration."""
    for child in node.named_children:
        if child.type == "function_declarator":
            return child
        # Recursively search in case it's nested
        result = _find_function_declarator(child)
        if result:
            return result
    return None


def _extract_type_alias_name(node, source_bytes: bytes) -> str:
    """Extract the name from a type alias (typedef/using declaration)."""
    if node.type == "type_definition":  # typedef
        # typedef usually has the alias name at the end
        for child in reversed(node.named_children):
            if child.type == "type_identifier":
                return source_bytes[child.start_byte : child.end_byte].decode("utf8")
    elif node.type == "alias_declaration":  # using
        # using alias_name = type;
        name_node = node.child_by_field_name("name")
        if name_node:
            return source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8")
    
    return None