import { z } from 'zod';
import { tool } from '@librechat/agents/langchain/tools';
import { toolValidationFeedback } from './validationFeedback';

describe('toolValidationFeedback', () => {
  it('explains a misrouted poll without invoking Bash or disclosing values', async () => {
    const execute = jest.fn(async () => 'executed');
    const bash = tool(execute, {
      name: 'bash_tool',
      description: 'Runs commands',
      schema: {
        type: 'object',
        properties: { command: { type: 'string' } },
        required: ['command'],
      },
    });
    const input = { background_task_id: 'private-task-id', intent: 'private-intent' };
    const error = await bash.invoke(input).catch((failure: Error) => failure);
    const feedback = toolValidationFeedback(error, bash.name, bash.schema, input, true);
    expect(execute).not.toHaveBeenCalled();
    expect(feedback).toContain('Missing required fields: command');
    expect(feedback).toContain('call check_background_task');
    expect(feedback).not.toContain('private-');
    expect(toolValidationFeedback(error, bash.name, bash.schema, input)).not.toContain(
      'check_background_task',
    );
  });

  it('does not rewrite execution failures that happen to use the parser message', () => {
    expect(
      toolValidationFeedback(
        new Error('Received tool input did not match expected schema'),
        'bash_tool',
      ),
    ).toBeUndefined();
  });

  it('reports primitive type mismatches using schema metadata only', async () => {
    const execute = jest.fn(async () => 'executed');
    const bash = tool(execute, {
      name: 'bash_tool',
      description: 'Runs commands',
      schema: {
        type: 'object',
        properties: { command: { type: 'string' } },
        required: ['command'],
      },
    });
    const input = { command: { secret: 'never-echo' } };
    const error = await bash.invoke(input).catch((failure: Error) => failure);
    const feedback = toolValidationFeedback(error, bash.name, bash.schema, input);
    expect(feedback).toContain('command (expected string)');
    expect(feedback).not.toContain('never-echo');
    expect(execute).not.toHaveBeenCalled();
  });

  it('reports missing fields from a generated Zod action schema without disclosing values', async () => {
    const execute = jest.fn(async () => 'executed');
    const searchSlots = tool(execute, {
      name: 'search-scheduling-slots_action_YXBpLWRldi',
      description: 'Search available scheduling slots',
      schema: z.object({
        startAt: z.string().min(1),
        endAt: z.string().min(1),
        eventTypeId: z.string().min(1).nullable().optional(),
        slug: z.string().min(1).nullable().optional(),
        timeZone: z.string().min(1).nullable().optional(),
      }),
    });
    const input = { private: 'never-echo' };
    const error = await searchSlots.invoke(input).catch((failure: Error) => failure);
    const feedback = toolValidationFeedback(error, searchSlots.name, searchSlots.schema, input);

    expect(execute).not.toHaveBeenCalled();
    expect(feedback).toContain('Missing required fields: startAt, endAt');
    expect(feedback).not.toContain('private');
    expect(feedback).not.toContain('never-echo');
  });

  it('reports invalid Zod field types but accepts null for nullable optional fields', async () => {
    const execute = jest.fn(async () => 'executed');
    const searchSlots = tool(execute, {
      name: 'search-scheduling-slots_action_YXBpLWRldi',
      description: 'Search available scheduling slots',
      schema: z.object({
        startAt: z.string().min(1),
        endAt: z.string().min(1),
        eventTypeId: z.string().min(1).nullable().optional(),
      }),
    });
    const input = { startAt: '2026-10-21T09:00:00-04:00', endAt: 42, eventTypeId: null };
    const error = await searchSlots.invoke(input).catch((failure: Error) => failure);
    const feedback = toolValidationFeedback(error, searchSlots.name, searchSlots.schema, input);

    expect(execute).not.toHaveBeenCalled();
    expect(feedback).toContain('endAt (expected string)');
    expect(feedback).not.toContain('eventTypeId');
  });
});
