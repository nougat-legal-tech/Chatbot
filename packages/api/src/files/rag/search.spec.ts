import { executeFileSearchQuery } from './search';

describe('executeFileSearchQuery', () => {
  it('returns safe retry guidance when every RAG request fails', async () => {
    const result = await executeFileSearchQuery({
      query: 'confidential legal question',
      userId: 'user-1',
      files: [{ file_id: 'file-1' }],
      fileCitations: true,
      ragApiUrl: 'https://rag.example.test',
      httpClient: {
        post: jest.fn().mockRejectedValue(new Error('provider credit_balance_exhausted')),
      },
      generateShortLivedToken: jest.fn().mockReturnValue('short-lived-token'),
      logAxiosError: jest.fn(),
      selectFileCitationSources: jest.fn().mockReturnValue([]),
      logger: { debug: jest.fn() },
    });

    expect(result).toEqual([
      'File search is temporarily unavailable. Tell the user you could not verify the answer against their files and ask them to retry. Do not present an answer as grounded in those files.',
      undefined,
    ]);
    expect(JSON.stringify(result)).not.toContain('credit_balance_exhausted');
    expect(JSON.stringify(result)).not.toContain('confidential legal question');
  });

  it('warns the model when results omit files whose retrieval failed', async () => {
    const result = await executeFileSearchQuery({
      query: 'compare these agreements',
      userId: 'user-1',
      files: [{ file_id: 'file-1' }, { file_id: 'file-2' }],
      fileCitations: false,
      ragApiUrl: 'https://rag.example.test',
      httpClient: {
        post: jest
          .fn()
          .mockResolvedValueOnce({
            data: [
              [
                {
                  page_content: 'The first agreement has a 30-day term.',
                  metadata: { source: 'first.pdf', page: 0 },
                },
                0.2,
              ],
            ],
          })
          .mockRejectedValueOnce(new Error('provider credit_balance_exhausted')),
      },
      generateShortLivedToken: jest.fn().mockReturnValue('short-lived-token'),
      logAxiosError: jest.fn(),
      selectFileCitationSources: jest.fn().mockReturnValue([]),
      logger: { debug: jest.fn() },
    });

    expect(result[0]).toContain('The first agreement has a 30-day term.');
    expect(result[0]).toContain('retrieval failed for 1 of 2 attached files');
    expect(result[0]).toContain('results are incomplete');
    expect(result[0]).not.toContain('credit_balance_exhausted');
    expect(result[0]).not.toContain('compare these agreements');
  });
});
