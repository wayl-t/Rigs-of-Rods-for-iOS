//-----------------------------------------------------------------------------
// Program Name: FFPLib_AlphaTest
// Program Desc: Alpha test function.
// Program Type: Vertex/Pixel shader
// Language: GLSL
//-----------------------------------------------------------------------------

//0 - CMPF_ALWAYS_FAIL,
//1 - CMPF_ALWAYS_PASS,
//2 - CMPF_LESS,
//3 - CMPF_LESS_EQUAL,
//4 - CMPF_EQUAL,
//5 - CMPF_NOT_EQUAL,
//6 - CMPF_GREATER_EQUAL,
//7 - CMPF_GREATER

// Apple/iOS port: GLSL ES 1.00 reserves 'switch' and 'case', so the upstream
// switch-chain fails to compile on GLES2 with "'switch' : Reserved word".
// That aborted RTSS shader generation and, because the exception escaped to
// RoRMain, took the whole app down with a red fatal-error box whenever a
// material used alpha_rejection (e.g. the Semi trailer).  An if/else chain is
// semantically identical and compiles on both desktop GLSL and GLSL ES.
bool Alpha_Func(in int func, in float alphaRef, in float alphaValue)
{
    if (func == 0)            // 0 - CMPF_ALWAYS_FAIL
        return false;
    else if (func == 1)       // 1 - CMPF_ALWAYS_PASS
        return true;
    else if (func == 2)       // 2 - CMPF_LESS
        return alphaValue < alphaRef;
    else if (func == 3)       // 3 - CMPF_LESS_EQUAL
        return alphaValue <= alphaRef;
    else if (func == 4)       // 4 - CMPF_EQUAL
        return alphaValue == alphaRef;
    else if (func == 5)       // 5 - CMPF_NOT_EQUAL
        return alphaValue != alphaRef;
    else if (func == 6)       // 6 - CMPF_GREATER_EQUAL
        return alphaValue >= alphaRef;
    else if (func == 7)       // 7 - CMPF_GREATER
        return alphaValue > alphaRef;
    return true;
}


void FFP_Alpha_Test(in float func, in float alphaRef, in vec4 texel)
{
    bool pass = Alpha_Func(int(func), alphaRef, texel.a);
    if (!pass)
        discard;
}
