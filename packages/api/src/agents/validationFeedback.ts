import type { StructuredToolInterface } from '@librechat/agents/langchain/tools';

const MAX_FIELDS = 8;
const safeName = (name: string): string => name.replace(/[^a-zA-Z0-9_.-]/g, '_').slice(0, 80);

type SchemaField = {
  _def?: { innerType?: unknown; schema?: unknown; typeName?: unknown };
  isOptional?: () => boolean;
};

function getZodShape(schema: unknown): Record<string, unknown> | undefined {
  if (schema == null || typeof schema !== 'object' || !('shape' in schema)) {
    return undefined;
  }
  const shape = (schema as { shape?: unknown }).shape;
  const resolved = typeof shape === 'function' ? shape() : shape;
  return resolved != null && typeof resolved === 'object'
    ? (resolved as Record<string, unknown>)
    : undefined;
}

function unwrapZodField(field: unknown): { optional: boolean; nullable: boolean; type?: string } {
  let current = field as SchemaField | undefined;
  let optional = false;
  let nullable = false;
  for (let depth = 0; depth < 8 && current != null && typeof current === 'object'; depth += 1) {
    const definition = current._def;
    if (definition == null || typeof definition !== 'object') {
      break;
    }
    const typeName = definition.typeName;
    if (typeName === 'ZodOptional' || typeName === 'ZodDefault' || typeName === 'ZodCatch') {
      optional = true;
    }
    if (typeName === 'ZodNullable') nullable = true;
    if (typeName === 'ZodString') return { optional, nullable, type: 'string' };
    if (typeName === 'ZodNumber') return { optional, nullable, type: 'number' };
    if (typeName === 'ZodBoolean') return { optional, nullable, type: 'boolean' };
    const inner = definition.innerType ?? definition.schema;
    if (inner == null || typeof inner !== 'object') {
      break;
    }
    current = inner as SchemaField;
  }
  return { optional, nullable };
}

/** Read only schema-owned field names; parser messages/output can contain input secrets. */
export function toolValidationFeedback(
  error: unknown,
  toolName: string,
  schema?: StructuredToolInterface['schema'],
  input?: unknown,
  canCheckBackground = false,
): string | undefined {
  if (
    !(error instanceof Error) ||
    error.constructor.name !== 'ToolInputParsingException' ||
    !error.message.startsWith('Received tool input did not match expected schema')
  ) {
    return undefined;
  }
  const fields: string[] = [];
  if (schema != null && typeof schema === 'object' && 'required' in schema) {
    const required = schema.required;
    if (Array.isArray(required)) {
      for (const field of required.slice(0, MAX_FIELDS)) {
        if (
          typeof field === 'string' &&
          (input == null ||
            typeof input !== 'object' ||
            !Object.prototype.hasOwnProperty.call(input, field))
        ) {
          fields.push(safeName(field));
        }
      }
    }
  }
  const shape = getZodShape(schema);
  if (shape != null) {
    for (const [field, fieldSchema] of Object.entries(shape).slice(0, MAX_FIELDS)) {
      const metadata = unwrapZodField(fieldSchema);
      if (
        !metadata.optional &&
        (input == null ||
          typeof input !== 'object' ||
          !Object.prototype.hasOwnProperty.call(input, field))
      ) {
        fields.push(safeName(field));
      }
    }
  }
  const uniqueFields = [...new Set(fields)];
  const missing = uniqueFields.length
    ? ` Missing required fields: ${uniqueFields.join(', ')}.`
    : '';
  const invalid: string[] = [];
  if (
    schema != null &&
    typeof schema === 'object' &&
    'properties' in schema &&
    schema.properties != null &&
    typeof schema.properties === 'object' &&
    input != null &&
    typeof input === 'object'
  ) {
    for (const [field, property] of Object.entries(schema.properties).slice(0, MAX_FIELDS)) {
      if (
        !Object.prototype.hasOwnProperty.call(input, field) ||
        property == null ||
        typeof property !== 'object' ||
        !('type' in property)
      )
        continue;
      const type = property.type;
      const value = Object.getOwnPropertyDescriptor(input, field)?.value;
      if ((type === 'string' || type === 'boolean' || type === 'number') && typeof value !== type) {
        invalid.push(`${safeName(field)} (expected ${type})`);
      }
    }
  }
  if (shape != null && input != null && typeof input === 'object') {
    for (const [field, fieldSchema] of Object.entries(shape).slice(0, MAX_FIELDS)) {
      const metadata = unwrapZodField(fieldSchema);
      const expected = metadata.type;
      const value = Object.getOwnPropertyDescriptor(input, field)?.value;
      if (
        expected != null &&
        value !== undefined &&
        !(value === null && metadata.nullable) &&
        typeof value !== expected
      ) {
        invalid.push(`${safeName(field)} (expected ${expected})`);
      }
    }
  }
  const uniqueInvalid = [...new Set(invalid)];
  const invalidTypes = uniqueInvalid.length
    ? ` Invalid field types: ${uniqueInvalid.join(', ')}.`
    : '';
  const polling =
    canCheckBackground &&
    input != null &&
    typeof input === 'object' &&
    Object.prototype.hasOwnProperty.call(input, 'background_task_id')
      ? ' To inspect an existing background task, call check_background_task with background_task_id.'
      : '';
  return `Tool "${safeName(toolName)}" input failed schema validation.${missing}${invalidTypes} Use this tool's declared arguments.${polling}`;
}
