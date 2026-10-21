import unittest
from unittest.mock import AsyncMock,patch
import httpcore
import httpx
from service_transport import PinnedBackend,GuardedTransport,TargetError

class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_dns_answers_rejected_before_tcp_connect(self):
        answers=[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('169.254.169.254',443))]
        with patch('asyncio.base_events.BaseEventLoop.getaddrinfo',new=AsyncMock(return_value=answers)),patch('service_transport.AnyIOBackend.connect_tcp',new=AsyncMock()) as tcp:
            with self.assertRaises(TargetError):await PinnedBackend(False,'https').connect_tcp('example.com',443)
            tcp.assert_not_awaited()

    async def test_only_validated_numeric_address_reaches_tcp_backend(self):
        answers=[(2,1,6,'',('8.8.8.8',443))]
        with patch('asyncio.base_events.BaseEventLoop.getaddrinfo',new=AsyncMock(return_value=answers)),patch('service_transport.AnyIOBackend.connect_tcp',new=AsyncMock(return_value='stream')) as tcp:
            result=await PinnedBackend(False,'https').connect_tcp('example.com',443)
            self.assertEqual(result,'stream');self.assertEqual(tcp.await_args.args[0],'8.8.8.8')

    async def test_sse_cross_origin_send_is_rejected_before_pool(self):
        transport=GuardedTransport('https://example.com/sse',False)
        try:
            with patch.object(transport.pool,'handle_async_request',new=AsyncMock()) as pool:
                with self.assertRaises(TargetError):await transport.handle_async_request(httpx.Request('POST','https://evil.example/messages'))
                pool.assert_not_awaited()
        finally:await transport.aclose()

    async def test_redirect_response_is_rejected_even_if_sdk_follows_redirects(self):
        transport=GuardedTransport('https://example.com/mcp',False)
        try:
            with patch.object(transport.pool,'handle_async_request',new=AsyncMock(return_value=httpcore.Response(307,headers=[(b'location',b'https://example.com/elsewhere')],content=b''))):
                with self.assertRaises(TargetError):await transport.handle_async_request(httpx.Request('POST','https://example.com/mcp',json={}))
        finally:await transport.aclose()

    async def test_httpcore_response_is_readable_and_original_hostname_preserved(self):
        transport=GuardedTransport('https://example.com/mcp',False)
        class Stream:
            async def __aiter__(self):yield b'{"ok":true}'
            async def aclose(self):pass
        async def handle(request):
            self.assertEqual(request.url.host,b'example.com')
            return httpcore.Response(200,headers=[(b'content-type',b'application/json')],content=Stream())
        try:
            with patch.object(transport.pool,'handle_async_request',side_effect=handle):
                response=await transport.handle_async_request(httpx.Request('POST','https://example.com/mcp',json={}))
                self.assertEqual(await response.aread(),b'{"ok":true}')
        finally:await transport.aclose()

if __name__=='__main__':unittest.main()
