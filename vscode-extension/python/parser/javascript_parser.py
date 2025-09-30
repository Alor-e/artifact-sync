from tree_sitter_language_pack import get_parser

def extract_headers_and_bodies(file_path: str) -> list[dict]:
    """
    Parses the given JavaScript file and returns a list of dicts, each containing:
      - node_type: one of
          'import_statement', 'export_statement', 'export_default_statement',
          'function_declaration', 'generator_function_declaration', 'class_declaration',
          'method_definition', 'function_assignment', 'constant_declaration'
      - name: identifier for functions/classes/variables (None for imports/exports)
      - header: signature or full import/export/declaration line
      - body: function/class body (None for imports, exports, simple variables)
    """
    with open(file_path, "rb") as f:
        source_bytes = f.read()

    parser = get_parser("javascript")
    tree = parser.parse(source_bytes)
    root = tree.root_node

    results = []
    for node in root.named_children:
        nt = node.type

        # 1. Import statements
        if nt == "import_statement":
            header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
            results.append({
                "node_type": nt,
                "name": None,
                "header": header,
                "body": None
            })

        # 2. Export statements
        elif nt in ("export_statement", "export_default_statement"):
            header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
            results.append({
                "node_type": nt,
                "name": None,
                "header": header,
                "body": None
            })

        # 3. Function declarations
        elif nt == "function_declaration":
            _process_js_function(node, source_bytes, results, "function_declaration")

        # 4. Generator function declarations
        elif nt == "generator_function_declaration":
            _process_js_function(node, source_bytes, results, "generator_function_declaration")

        # 5. Class declarations
        elif nt == "class_declaration":
            _process_js_class(node, source_bytes, results)

        # 6. Variable declarations - only significant ones
        elif nt == "variable_declaration":
            _process_significant_variables(node, source_bytes, results)

        # 7. Expression statements - only function assignments
        elif nt == "expression_statement":
            _process_function_assignments(node, source_bytes, results)

    return results


def _process_js_function(func_node, source_bytes: bytes, out_list: list, node_type: str):
    """Process function_declaration or generator_function_declaration nodes."""
    name_node = func_node.child_by_field_name("name")
    name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8") if name_node else None

    body_node = func_node.child_by_field_name("body")
    if body_node:
        header = source_bytes[func_node.start_byte : body_node.start_byte].decode("utf8")
        body = source_bytes[body_node.start_byte : func_node.end_byte].decode("utf8")
    else:
        header = source_bytes[func_node.start_byte : func_node.end_byte].decode("utf8")
        body = None

    out_list.append({
        "node_type": node_type,
        "name": name,
        "header": header,
        "body": body
    })


def _process_js_class(class_node, source_bytes: bytes, out_list: list):
    """Process class_declaration nodes and their methods."""
    name_node = class_node.child_by_field_name("name")
    name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8") if name_node else None

    body_node = class_node.child_by_field_name("body")
    if body_node:
        header = source_bytes[class_node.start_byte : body_node.start_byte].decode("utf8")
        body = source_bytes[body_node.start_byte : class_node.end_byte].decode("utf8")
    else:
        header = source_bytes[class_node.start_byte : class_node.end_byte].decode("utf8")
        body = None

    out_list.append({
        "node_type": "class_declaration",
        "name": name,
        "header": header,
        "body": body
    })

    # Process class methods
    if body_node:
        for member in body_node.named_children:
            if member.type == "method_definition":
                _process_js_method(member, source_bytes, out_list)


def _process_js_method(method_node, source_bytes: bytes, out_list: list):
    """Process method_definition nodes within classes."""
    name_node = method_node.child_by_field_name("name")
    name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8") if name_node else None

    body_node = method_node.child_by_field_name("body")
    if body_node:
        header = source_bytes[method_node.start_byte : body_node.start_byte].decode("utf8")
        body = source_bytes[body_node.start_byte : method_node.end_byte].decode("utf8")
    else:
        header = source_bytes[method_node.start_byte : method_node.end_byte].decode("utf8")
        body = None

    out_list.append({
        "node_type": "method_definition",
        "name": name,
        "header": header,
        "body": body
    })


def _process_significant_variables(var_node, source_bytes: bytes, out_list: list):
    """
    Process variable declarations, but only capture:
    1. Function expressions/arrow functions assigned to variables
    2. Constants that might be configuration or important values
    """
    for child in var_node.named_children:
        if child.type == "variable_declarator":
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            
            if name_node:
                name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8")
                
                # Check if it's a function assignment
                if value_node and value_node.type in ("function_expression", "arrow_function", "generator_function"):
                    header = source_bytes[var_node.start_byte : var_node.end_byte].decode("utf8")
                    
                    func_body_node = value_node.child_by_field_name("body")
                    if func_body_node and func_body_node.type == "statement_block":
                        func_body = source_bytes[func_body_node.start_byte : func_body_node.end_byte].decode("utf8")
                    else:
                        func_body = None
                    
                    out_list.append({
                        "node_type": "function_assignment",
                        "name": name,
                        "header": header,
                        "body": func_body
                    })
                    return
                
                # Check if it's a constant (const declaration or SCREAMING_SNAKE_CASE)
                elif (_is_constant_declaration(var_node, source_bytes) or 
                      _is_constant_name(name)):
                    header = source_bytes[var_node.start_byte : var_node.end_byte].decode("utf8")
                    out_list.append({
                        "node_type": "constant_declaration",
                        "name": name,
                        "header": header,
                        "body": None
                    })
                    return


def _process_function_assignments(expr_stmt_node, source_bytes: bytes, out_list: list):
    """
    Process expression statements that assign functions to object properties,
    focusing on module.exports and similar patterns.
    """
    expr_node = expr_stmt_node.child_by_field_name("expression") or expr_stmt_node.named_children[0]
    
    if expr_node and expr_node.type == "assignment_expression":
        left_node = expr_node.child_by_field_name("left")
        right_node = expr_node.child_by_field_name("right")
        
        if left_node and right_node:
            left_text = source_bytes[left_node.start_byte : left_node.end_byte].decode("utf8")
            
            # Only capture if it's assigning a function and looks like an export
            if (right_node.type in ("function_expression", "arrow_function", "generator_function") and
                _is_export_pattern(left_text)):
                
                name = left_text.split(".")[-1] if "." in left_text else left_text
                header = source_bytes[expr_stmt_node.start_byte : expr_stmt_node.end_byte].decode("utf8")
                
                func_body_node = right_node.child_by_field_name("body")
                if func_body_node and func_body_node.type == "statement_block":
                    func_body = source_bytes[func_body_node.start_byte : func_body_node.end_byte].decode("utf8")
                else:
                    func_body = None
                
                out_list.append({
                    "node_type": "function_assignment",
                    "name": name,
                    "header": header,
                    "body": func_body
                })


def _is_constant_declaration(var_node, source_bytes: bytes) -> bool:
    """Check if this is a const declaration."""
    declaration_text = source_bytes[var_node.start_byte : var_node.end_byte].decode("utf8")
    return declaration_text.strip().startswith("const")


def _is_constant_name(name: str) -> bool:
    """Check if the variable name looks like a constant (SCREAMING_SNAKE_CASE)."""
    return name.isupper() and "_" in name and len(name) > 3


def _is_export_pattern(left_text: str) -> bool:
    """Check if the assignment target looks like an export pattern."""
    export_patterns = [
        "module.exports",
        "exports.",
        "window.",
        "global.",
        # Add other patterns as needed
    ]
    return any(left_text.startswith(pattern) for pattern in export_patterns)