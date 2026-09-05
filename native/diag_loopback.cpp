/**
 * diag_loopback.cpp -- Diagnóstico standalone de ActivateAudioInterfaceAsync
 *
 * Compilar:
 *   g++ -O0 -std=c++11 -Wno-attributes -o diag_loopback.exe diag_loopback.cpp
 *       -lole32 -static -static-libgcc -static-libstdc++
 *
 * Uso:
 *   diag_loopback.exe <PID>
 */

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <objbase.h>
#include <cstdio>
#include <cstring>

#ifndef WAVE_FORMAT_IEEE_FLOAT
#define WAVE_FORMAT_IEEE_FLOAT 3
#endif
#ifndef _WAVEFORMATEX_
#define _WAVEFORMATEX_
typedef struct _wfx {
    WORD  wFormatTag; WORD nChannels; DWORD nSamplesPerSec;
    DWORD nAvgBytesPerSec; WORD nBlockAlign; WORD wBitsPerSample; WORD cbSize;
} WAVEFORMATEX;
#endif

/* GUIDs */
static const GUID MY_IID_IUnknown =
    {0x00000000,0x0000,0x0000,{0xC0,0x00,0x00,0x00,0x00,0x00,0x00,0x46}};
static const GUID MY_IID_IAudioClient =
    {0x1CB9AD4C,0xDBFA,0x4C32,{0xB1,0x78,0xC2,0xF5,0x68,0xA7,0x03,0xB2}};
static const GUID MY_IID_ICompletionHandler =
    {0x41D949AB,0x9862,0x444A,{0x80,0xF6,0xC2,0x61,0x33,0x4D,0xA5,0xEB}};

/* AUDIOCLIENT_ACTIVATION_PARAMS */
typedef enum { AUDCLNT_ACT_DEFAULT=0, AUDCLNT_ACT_PROCESS=1 } MY_ACT_TYPE;
typedef enum { LOOP_INCLUDE=0, LOOP_EXCLUDE=1 }               MY_LOOP_MODE;
typedef struct { DWORD TargetProcessId; MY_LOOP_MODE ProcessLoopbackMode; } MY_LOOP;
typedef struct { MY_ACT_TYPE ActivationType; union { MY_LOOP ProcessLoopbackParams; }; } MY_PARAMS;

/* Interfaces COM */
MIDL_INTERFACE("72A22D78-CDE4-431D-B8CC-843A71199B6D")
IMyAsync : public IUnknown {
public:
    virtual HRESULT STDMETHODCALLTYPE GetActivateResult(HRESULT*,IUnknown**)=0;
};
MIDL_INTERFACE("41D949AB-9862-444A-80F6-C261334DA5EB")
IMyHandler : public IUnknown {
public:
    virtual HRESULT STDMETHODCALLTYPE ActivateCompleted(IMyAsync*)=0;
};

typedef HRESULT(WINAPI* PFN_Act)(LPCWSTR,REFIID,PROPVARIANT*,IMyHandler*,IMyAsync**);

class Handler : public IMyHandler {
    LONG _ref=1;
public:
    HANDLE done; HRESULT hr; IUnknown* result;
    Handler(): done(CreateEventW(nullptr,TRUE,FALSE,nullptr)),hr(E_PENDING),result(nullptr){}
    ~Handler(){ CloseHandle(done); if(result) result->Release(); }
    ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&_ref); }
    ULONG STDMETHODCALLTYPE Release() override {
        LONG r=InterlockedDecrement(&_ref); if(!r) delete this; return r;
    }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid,void** ppv) override {
        static const GUID IID_IAgileObject = {0x94ea2b94, 0xe9cc, 0x49e0, {0xc0, 0xff, 0xee, 0x64, 0xca, 0x8f, 0x5b, 0x90}};
        if(!memcmp(&riid,&MY_IID_IUnknown,16)||
           !memcmp(&riid,&MY_IID_ICompletionHandler,16)||
           !memcmp(&riid,&IID_IAgileObject,16))
        { *ppv=static_cast<IMyHandler*>(this); AddRef(); return S_OK; }
        
        /* Implement FTM just in case IAgileObject is not enough */
        if (riid.Data1 == 0x00000003 && riid.Data2 == 0 && riid.Data3 == 0) { // IID_IMarshal
            if (!m_pMarshaler) CoCreateFreeThreadedMarshaler(static_cast<IMyHandler*>(this), &m_pMarshaler);
            if (m_pMarshaler) return m_pMarshaler->QueryInterface(riid, ppv);
        }
        *ppv=nullptr; return E_NOINTERFACE;
    }
    IUnknown* m_pMarshaler = nullptr;
    HRESULT STDMETHODCALLTYPE ActivateCompleted(IMyAsync* op) override {
        HRESULT res=S_OK; IUnknown* unk=nullptr;
        op->GetActivateResult(&res,&unk);
        hr=res; result=unk;
        SetEvent(done);
        return S_OK;
    }
};

int main(int argc, char** argv)
{
    DWORD pid = (argc>=2) ? (DWORD)atoi(argv[1]) : GetCurrentProcessId();
    printf("Diagnostico WASAPI Process Loopback\n");
    printf("PID objetivo: %lu\n", pid);

    /* STA requerido */
    HRESULT hr = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    printf("CoInitialize STA: 0x%08lX %s\n", (unsigned long)hr,
           SUCCEEDED(hr)||hr==(HRESULT)0x80010106L?"OK":"FALLO");

    HMODULE hMM = LoadLibraryW(L"mmdevapi.dll");
    if(!hMM){ printf("ERROR: no se pudo cargar mmdevapi.dll\n"); return 1; }
    PFN_Act pfn = (PFN_Act)GetProcAddress(hMM,"ActivateAudioInterfaceAsync");
    printf("ActivateAudioInterfaceAsync: %s\n", pfn?"OK":"NO ENCONTRADO");
    if(!pfn){ FreeLibrary(hMM); return 1; }

    /* Tamaño de la estructura */
    printf("sizeof(MY_PARAMS) = %zu bytes (esperado: 12)\n", sizeof(MY_PARAMS));

    static const WCHAR VIRT_ID[] = L"VAD\\Process_Loopback";
    printf("Device String: VAD\\Process_Loopback\n");

    MY_PARAMS params={};
    params.ActivationType=AUDCLNT_ACT_PROCESS;
    params.ProcessLoopbackParams.TargetProcessId=pid;
    params.ProcessLoopbackParams.ProcessLoopbackMode=LOOP_INCLUDE;

    PROPVARIANT pv={};
    pv.vt=VT_BLOB;
    pv.blob.cbSize=(ULONG)sizeof(MY_PARAMS);
    pv.blob.pBlobData=(BYTE*)&params;

    printf("pv.blob.cbSize = %lu\n",(unsigned long)pv.blob.cbSize);

    Handler* h = new Handler();
    IMyAsync* op=nullptr;
    hr = pfn(VIRT_ID,MY_IID_IAudioClient,&pv,h,&op);
    printf("pfnActivate retorno: 0x%08lX %s\n",(unsigned long)hr,SUCCEEDED(hr)?"OK":"FALLO");
    FreeLibrary(hMM);
    if(FAILED(hr)){ h->Release(); CoUninitialize(); return 1; }

    /* Pump STA */
    MSG msg; DWORD w;
    printf("Esperando completion callback (max 5s)...\n");
    while((w=MsgWaitForMultipleObjects(1,&h->done,FALSE,5000,QS_ALLEVENTS))==WAIT_OBJECT_0+1){
        while(PeekMessageW(&msg,nullptr,0,0,PM_REMOVE)){
            TranslateMessage(&msg); DispatchMessageW(&msg);
        }
    }

    printf("result_hr: 0x%08lX %s\n",(unsigned long)h->hr,
           SUCCEEDED(h->hr)?"OK":"FALLO");
    printf("audio_client ptr: %p\n",(void*)h->result);

    if(SUCCEEDED(h->hr)&&h->result){
        printf("Activacion exitosa! IAudioClient obtenido.\n");
    } else {
        printf("La activacion fallo.\n");
        if(h->hr==(HRESULT)0x8000000E)
            printf("  0x8000000E = E_ILLEGAL_METHOD_CALL\n");
        else if(h->hr==(HRESULT)0x80070057)
            printf("  0x80070057 = E_INVALIDARG (estructura mal formada o PID invalido)\n");
        else if(h->hr==(HRESULT)0x88890011)
            printf("  0x88890011 = AUDCLNT_E_DEVICE_IN_USE\n");
    }
    if(op) op->Release();
    h->Release();
    CoUninitialize();
    return 0;
}
